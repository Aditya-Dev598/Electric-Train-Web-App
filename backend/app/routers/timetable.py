"""FastAPI router for timetable generation endpoints."""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
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

# In-memory result cache (keyed by job/generation ID)
# For production, use Redis or similar persistent store
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


class GenerateRequest(BaseModel):
    station_name: str = Field(..., min_length=1, max_length=100)
    operator_code: str = Field(..., min_length=2, max_length=3)
    date_start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_end: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


def _run_generation(
    job_id: str,
    raw_bytes: bytes,
    filename: str,
    station: str,
    operator: str,
    start_date: date,
    end_date: date,
    state,
) -> None:
    """CPU-bound CIF parsing and timetable generation, executed in a thread pool."""
    try:
        text = raw_bytes.decode("utf-8", errors="replace")
        del raw_bytes  # release memory as soon as possible

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

        orchestrator = Orchestrator(
            corpus=state.corpus,
            cif_parser=upload_parser,
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

        _result_cache[job_id] = {
            "timetable_csv": timetable_csv,
            "route_csv": route_csv,
            "debug_csv": debug_csv,
        }

        # Limit cache size
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
                    "distance_miles": r.distance_miles,
                    "run_min": r.run_min,
                    "wait_min": r.wait_min,
                }
                for r in result.route_rows[:20]
            ],
        }

        # Limit jobs store size
        if len(_jobs) > 200:
            oldest = next(iter(_jobs))
            del _jobs[oldest]

    except Exception as exc:
        _jobs[job_id] = {"status": "error", "detail": str(exc)}


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
        return JSONResponse(
            status_code=422,
            content={"valid": False, "errors": errors},
        )

    return JSONResponse(content={"valid": True, "errors": []})


@router.post("/generate")
async def generate_csv(
    request: Request,
    cif_file: UploadFile = File(..., description="CIF/MCA timetable file from Network Rail"),
    station_name: str = Form(..., min_length=1, max_length=100),
    operator_code: str = Form(..., min_length=2, max_length=3),
    date_start: str = Form(...),
    date_end: Optional[str] = Form(None),
) -> JSONResponse:
    """Accept a CIF upload, start async generation, and return a job ID immediately.

    Use GET /api/generate/status/{job_id} to poll for completion.
    """
    # Validate inputs (fast, no I/O)
    try:
        station = validate_station_name(station_name)
        operator = validate_operator_code(operator_code)
        start_date = validate_date(date_start)
        end_date = validate_date(date_end) if date_end else start_date
        if date_end:
            validate_date_range(date_start, date_end)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"{e.field}: {e.message}")

    # Read the uploaded file (async, releases back-pressure to the client)
    raw_bytes = await cif_file.read()
    filename = cif_file.filename or "upload.CIF"

    # Cheap LFS pointer check before spinning up the thread
    first_line = raw_bytes[:200].decode("utf-8", errors="replace").split("\n", 1)[0].rstrip()
    if first_line.startswith("version https://git-lfs.github.com"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Uploaded file is a Git LFS pointer, not the actual CIF data. "
                "Run 'git lfs pull' to download the real file before uploading."
            ),
        )

    # Queue the CPU-bound work in the thread pool and return immediately
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "processing"}

    loop = asyncio.get_event_loop()
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
        request.app.state,
    )

    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "processing"})


@router.get("/generate/status/{job_id}")
async def get_generation_status(job_id: str) -> JSONResponse:
    """Poll the status of an async generation job.

    Returns {"status": "processing"} while running,
    the full generation result when status is "done",
    or {"status": "error", "detail": "..."} on failure.
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    return JSONResponse(content=job)


@router.get("/download/{gen_id}/timetable.csv")
async def download_timetable(gen_id: str) -> Response:
    """Download the timetable CSV for a generation result."""
    cached = _result_cache.get(gen_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Generation result not found or expired")

    return Response(
        content=cached["timetable_csv"],
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=timetable.csv",
        },
    )


@router.get("/download/{gen_id}/route.csv")
async def download_route(gen_id: str) -> Response:
    """Download the route CSV for a generation result."""
    cached = _result_cache.get(gen_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Generation result not found or expired")

    return Response(
        content=cached["route_csv"],
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=route.csv",
        },
    )


@router.get("/download/{gen_id}/debug.csv")
async def download_debug(gen_id: str) -> Response:
    """Download the debug/diagnostic CSV for a generation result."""
    cached = _result_cache.get(gen_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Generation result not found or expired")

    return Response(
        content=cached["debug_csv"],
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=debug.csv",
        },
    )
