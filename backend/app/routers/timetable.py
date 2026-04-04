"""FastAPI router for timetable generation endpoints."""

from __future__ import annotations

import asyncio
import csv
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from backend.app.security.validators import (
    ValidationError,
    validate_date,
    validate_date_range,
    validate_operator_code,
    validate_station_name,
)
from backend.app.services.cif_parser import CIFParser
from backend.app.services.csv_exporter import (
    generate_debug_csv,
    generate_route_csv,
    generate_timetable_csv,
)
from backend.app.services.orchestrator import Orchestrator

router = APIRouter(prefix="/api", tags=["timetable"])

# In-memory result cache (keyed by job/generation ID) — fast path for fresh results
_result_cache: dict[str, dict] = {}

# In-memory job status store
_jobs: dict[str, dict] = {}

# Thread pool for CPU-bound CIF parsing (max 2 concurrent jobs)
_executor = ThreadPoolExecutor(max_workers=2)


class ValidateRequest(BaseModel):
    station_name: str = Field(..., min_length=1, max_length=100)
    operator_code: str = Field(..., min_length=2, max_length=3)
    date_start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_end: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


def _csv_to_rows(csv_text: str) -> list[dict[str, str]]:
    """Parse a CSV string into a list of dicts (all values as strings)."""
    reader = csv.DictReader(io.StringIO(csv_text))
    return [dict(row) for row in reader]


def _rows_to_csv(rows: list[dict[str, str]], fieldnames: list[str]) -> str:
    """Serialise a list of dicts back to a CSV string."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _run_generation(
    job_id: str,
    raw_bytes: Optional[bytes],
    filename: str,
    station: str,
    operator: str,
    start_date: date,
    end_date: date,
    state,
    use_server_cif: bool = False,
) -> None:
    """CPU-bound CIF parsing and timetable generation, executed in a thread pool."""
    try:
        if use_server_cif:
            cif_parser = state.cif_parser
        else:
            text = raw_bytes.decode("utf-8", errors="replace")
            del raw_bytes

            upload_parser = CIFParser()
            upload_parser.parse_lines(text.splitlines(), source=filename)
            del text

            if not upload_parser.schedules:
                _jobs[job_id] = {
                    "status": "error",
                    "detail": (
                        f"No CIF schedules found in uploaded file '{filename}'. "
                        "Ensure the file is a valid Network Rail CIF/MCA timetable."
                    ),
                }
                return
            cif_parser = upload_parser

        orchestrator = Orchestrator(
            corpus=state.corpus,
            cif_parser=cif_parser,
            mileage=state.mileage,
            darwin=state.darwin,
            audit=state.audit,
        )

        result = orchestrator.generate(
            station_name=station,
            operator_code=operator,
            date_start=start_date,
            date_end=end_date,
        )

        timetable_csv = generate_timetable_csv(result.timetable_rows)
        route_csv = generate_route_csv(result.route_rows)
        debug_csv = generate_debug_csv(result.debug_rows)

        # Persist to disk
        metadata = {
            "station_name": station,
            "operator_code": operator,
            "date_start": str(start_date),
            "date_end": str(end_date),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "timetable_rows": len(result.timetable_rows),
            "route_rows": len(result.route_rows),
            "warnings": result.warnings,
            "provenance": result.provenance,
            "summary": result.summary,
        }
        state.result_store.save(job_id, timetable_csv, route_csv, debug_csv, metadata)

        # Also keep in memory for fast access
        _result_cache[job_id] = {
            "timetable_csv": timetable_csv,
            "route_csv": route_csv,
            "debug_csv": debug_csv,
        }
        if len(_result_cache) > 100:
            oldest = next(iter(_result_cache))
            del _result_cache[oldest]

        _jobs[job_id] = {
            "status": "done",
            "generation_id": job_id,
            "timetable_rows": len(result.timetable_rows),
            "route_rows": len(result.route_rows),
            "warnings": result.warnings,
            "provenance": result.provenance,
            "summary": result.summary,
            "timetable_preview": [
                {
                    "date": r.date,
                    "departure_time": r.departure_time,
                    "route_variant": r.route_variant,
                    "stop_type": r.stop_type,
                    "train_class": r.train_class,
                    "number_of_coaches": r.number_of_coaches,
                }
                for r in result.timetable_rows[:20]
            ],
            "route_preview": [
                {
                    "route_variant": r.route_variant,
                    "seq": r.seq,
                    "from_station": r.from_station,
                    "to_station": r.to_station,
                    "stop_type": r.stop_type,
                    "distance_miles": r.distance_miles,
                    "run_min": r.run_min,
                    "wait_min": r.wait_min,
                }
                for r in result.route_rows[:20]
            ],
        }

        if len(_jobs) > 200:
            oldest = next(iter(_jobs))
            del _jobs[oldest]

    except Exception as exc:
        _jobs[job_id] = {"status": "error", "detail": str(exc)}


def _get_csv(gen_id: str, csv_type: str, state) -> str:
    """Load CSV from memory cache then disk; raises 404 if not found."""
    cached = _result_cache.get(gen_id)
    if cached and f"{csv_type}_csv" in cached:
        return cached[f"{csv_type}_csv"]
    content = state.result_store.load_csv(gen_id, csv_type)
    if content is None:
        raise HTTPException(status_code=404, detail="Generation result not found")
    return content


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@router.post("/validate")
async def validate_inputs(req: ValidateRequest) -> JSONResponse:
    """Validate all user inputs before generation."""
    errors = []

    try:
        validate_station_name(req.station_name)
    except ValidationError as e:
        errors.append({"field": e.field, "message": e.message})

    try:
        validate_operator_code(req.operator_code)
    except ValidationError as e:
        errors.append({"field": e.field, "message": e.message})

    try:
        validate_date(req.date_start)
        if req.date_end:
            validate_date_range(req.date_start, req.date_end)
    except ValidationError as e:
        errors.append({"field": e.field, "message": e.message})

    if errors:
        return JSONResponse(status_code=422, content={"valid": False, "errors": errors})
    return JSONResponse(content={"valid": True, "errors": []})


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

@router.post("/generate")
async def generate_csv(
    request: Request,
    cif_file: Optional[UploadFile] = File(None, description="CIF/MCA timetable file (optional if server CIF already loaded)"),
    station_name: str = Form(..., min_length=1, max_length=100),
    operator_code: str = Form(..., min_length=2, max_length=3),
    date_start: str = Form(...),
    date_end: Optional[str] = Form(None),
) -> JSONResponse:
    """Accept an optional CIF upload, start async generation, and return a job ID immediately."""
    try:
        station = validate_station_name(station_name)
        operator = validate_operator_code(operator_code)
        start_date = validate_date(date_start)
        end_date = validate_date(date_end) if date_end else start_date
        if date_end:
            validate_date_range(date_start, date_end)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"{e.field}: {e.message}")

    state = request.app.state

    if cif_file is not None:
        raw_bytes = await cif_file.read()
        filename = cif_file.filename or "upload.CIF"
        first_line = raw_bytes[:200].decode("utf-8", errors="replace").split("\n", 1)[0].rstrip()
        if first_line.startswith("version https://git-lfs.github.com"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Uploaded file is a Git LFS pointer, not the actual CIF data. "
                    "Run 'git lfs pull' to download the real file before uploading."
                ),
            )
        use_server_cif = False
    else:
        if not state.cif_parser.schedules:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No CIF file provided and no server CIF is loaded. "
                    "Upload a CIF file via POST /api/cif/upload first, or include it in this request."
                ),
            )
        raw_bytes = None
        filename = getattr(state, "cif_filename", "server CIF")
        use_server_cif = True

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "processing"}

    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        _executor,
        _run_generation,
        job_id,
        raw_bytes,
        filename,
        station,
        operator,
        start_date,
        end_date,
        state,
        use_server_cif,
    )

    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "processing"})


@router.get("/generate/status/{job_id}")
async def get_generation_status(job_id: str) -> JSONResponse:
    """Poll the status of an async generation job."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return JSONResponse(content=job)


# ---------------------------------------------------------------------------
# Downloads (memory → disk fallback)
# ---------------------------------------------------------------------------

@router.get("/download/{gen_id}/timetable.csv")
async def download_timetable(gen_id: str, request: Request) -> Response:
    content = _get_csv(gen_id, "timetable", request.app.state)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=timetable.csv"},
    )


@router.get("/download/{gen_id}/route.csv")
async def download_route(gen_id: str, request: Request) -> Response:
    content = _get_csv(gen_id, "route", request.app.state)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=route.csv"},
    )


@router.get("/download/{gen_id}/debug.csv")
async def download_debug(gen_id: str, request: Request) -> Response:
    content = _get_csv(gen_id, "debug", request.app.state)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=debug.csv"},
    )


# ---------------------------------------------------------------------------
# Results history (Phase 2)
# ---------------------------------------------------------------------------

@router.get("/results")
async def list_results(request: Request) -> JSONResponse:
    """List all persisted generation results (newest first)."""
    results = request.app.state.result_store.list_results()
    return JSONResponse(content=results)


@router.delete("/results/{gen_id}")
async def delete_result(gen_id: str, request: Request) -> JSONResponse:
    """Delete a persisted result from disk."""
    deleted = request.app.state.result_store.delete(gen_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Result not found")
    _result_cache.pop(gen_id, None)
    return JSONResponse(content={"deleted": True})


# ---------------------------------------------------------------------------
# In-browser CSV editor endpoints (Phase 3)
# ---------------------------------------------------------------------------

@router.get("/results/{gen_id}/timetable.json")
async def get_timetable_json(gen_id: str, request: Request) -> JSONResponse:
    """Return full timetable CSV as JSON rows for the in-browser editor."""
    content = _get_csv(gen_id, "timetable", request.app.state)
    rows = _csv_to_rows(content)
    return JSONResponse(content=rows)


@router.get("/results/{gen_id}/route.json")
async def get_route_json(gen_id: str, request: Request) -> JSONResponse:
    """Return full route CSV as JSON rows for the in-browser editor."""
    content = _get_csv(gen_id, "route", request.app.state)
    rows = _csv_to_rows(content)
    return JSONResponse(content=rows)


@router.put("/results/{gen_id}/timetable")
async def put_timetable(gen_id: str, request: Request, rows: list[dict[str, Any]] = Body(...)) -> JSONResponse:
    """Save edited timetable rows back to disk."""
    state = request.app.state
    if not state.result_store.exists(gen_id):
        raise HTTPException(status_code=404, detail="Result not found")
    fieldnames = ["route_variant", "stop_type", "date", "departure_time", "train_class", "number_of_coaches"]
    csv_text = _rows_to_csv(rows, fieldnames)
    state.result_store.write_csv(gen_id, "timetable", csv_text)
    _result_cache.pop(gen_id, None)  # invalidate memory cache
    return JSONResponse(content={"saved": True, "rows": len(rows)})


@router.put("/results/{gen_id}/route")
async def put_route(gen_id: str, request: Request, rows: list[dict[str, Any]] = Body(...)) -> JSONResponse:
    """Save edited route rows back to disk."""
    state = request.app.state
    if not state.result_store.exists(gen_id):
        raise HTTPException(status_code=404, detail="Result not found")
    fieldnames = ["route_variant", "seq", "from_station", "to_station", "stop_type",
                  "distance_miles", "avg_elevation_m", "run_min", "wait_min"]
    csv_text = _rows_to_csv(rows, fieldnames)
    state.result_store.write_csv(gen_id, "route", csv_text)
    _result_cache.pop(gen_id, None)
    return JSONResponse(content={"saved": True, "rows": len(rows)})


@router.post("/results/{gen_id}/recalculate-distances")
async def recalculate_distances(
    gen_id: str,
    request: Request,
    rows: list[dict[str, Any]] = Body(...),
) -> JSONResponse:
    """Re-estimate distance_miles and avg_elevation_m for each route row.

    Resolution order (same as initial generation):
    1. Official mileage.json data (if loaded) keyed by TIPLOC pair
    2. Coordinate estimate (OSM Overpass + OpenTopoData) keyed by CRS codes

    Station display names are resolved → TIPLOC via CORPUS, then CRS for
    the coordinate fallback. Rows where stations cannot be resolved are
    returned unchanged.
    """
    state = request.app.state
    corpus = state.corpus
    mileage = state.mileage

    updated = []
    for row in rows:
        from_name = str(row.get("from_station", ""))
        to_name = str(row.get("to_station", ""))

        from_matches = corpus.resolve_station(from_name)
        to_matches = corpus.resolve_station(to_name)

        new_row = dict(row)
        if from_matches and to_matches:
            from_tiploc = from_matches[0].tiploc
            to_tiploc = to_matches[0].tiploc

            # Use full resolution chain: mileage.json → coordinate estimate
            dist, elev, _ = mileage.get_segment_with_elevation(from_tiploc, to_tiploc)
            # Only overwrite when we actually got a value — don't wipe existing data
            if dist is not None:
                new_row["distance_miles"] = round(dist, 2)
            if elev is not None:
                new_row["avg_elevation_m"] = round(elev, 1)
        updated.append(new_row)

    return JSONResponse(content={"rows": updated})
