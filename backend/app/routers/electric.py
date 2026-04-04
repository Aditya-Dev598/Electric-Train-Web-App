"""FastAPI router for the Electric Train energy pipeline.

Workflow:
  1. POST /api/electric/upload/{file_type}  — upload rolling_stock, station_points,
                                              timetable, or route CSV
  2. GET  /api/electric/status              — check which reference files are loaded
  3. POST /api/electric/run                 — returns { job_id } immediately (202)
  4. GET  /api/electric/job/{job_id}        — poll until status='done' or 'error'
  5. GET  /api/electric/output/{run_id}/{filename} — download a per-TSS output CSV
  6. GET  /api/electric/runs               — list all past runs
  7. POST /api/electric/merge               — merge selected results into a new stored result
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from backend.app.services.electric_pipeline import (
    combine_csvs,
    combine_route_csvs,
    run_pipeline,
)

router = APIRouter(prefix="/api/electric", tags=["electric"])
logger = logging.getLogger(__name__)

# In-memory stores
_electric_runs: dict[str, dict[str, str]] = {}   # run_id  → { tss_name: csv_str }
_electric_jobs: dict[str, dict] = {}              # job_id  → { status, run_id?, tss_files?, error? }
_electric_debug: dict[str, str] = {}             # run_id  → debug csv text (station mismatches)

_ALLOWED_FILE_TYPES = {"rolling_stock", "station_points", "timetable", "route"}


class RunRequest(BaseModel):
    result_ids: list[str] = []


class MergeRequest(BaseModel):
    result_ids: list[str]


def _get_electric_dir(state) -> Path:
    return Path(state.settings.electric_data_path)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload/{file_type}")
async def upload_electric_file(
    file_type: str,
    request: Request,
    file: UploadFile = File(...),
) -> JSONResponse:
    """Upload a reference CSV file for the Electric pipeline."""
    if file_type not in _ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown file_type '{file_type}'. Must be one of: {sorted(_ALLOWED_FILE_TYPES)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    electric_dir = _get_electric_dir(request.app.state)
    electric_dir.mkdir(parents=True, exist_ok=True)
    dest = electric_dir / f"{file_type}.csv"
    dest.write_bytes(content)
    logger.info("Electric reference file saved: %s", dest)

    return JSONResponse(content={"saved": file_type, "filename": file.filename or f"{file_type}.csv"})


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status")
async def electric_status(request: Request) -> JSONResponse:
    """Return which Electric pipeline reference files are currently uploaded."""
    electric_dir = _get_electric_dir(request.app.state)
    return JSONResponse(content={
        "rolling_stock": (electric_dir / "rolling_stock.csv").exists(),
        "station_points": (electric_dir / "station_points.csv").exists(),
        "timetable": (electric_dir / "timetable.csv").exists(),
        "route": (electric_dir / "route.csv").exists(),
    })


# ---------------------------------------------------------------------------
# Run (async job)
# ---------------------------------------------------------------------------

def _run_pipeline_job(job_id: str, timetable_parts: list[str], route_parts: list[str],
                      rolling_stock_path: Path, station_points_path: Path,
                      electric_dir: Path, state) -> None:
    """Blocking pipeline execution — runs in a thread-pool worker."""
    try:
        combined_tt = combine_csvs(timetable_parts)
        combined_route = combine_route_csvs(route_parts)
        tss_outputs, debug_csv = run_pipeline(
            timetable_csv=combined_tt,
            route_csv=combined_route,
            rolling_stock_path=rolling_stock_path,
            station_points_path=station_points_path,
        )
    except Exception as exc:
        logger.exception("Electric pipeline error in job %s", job_id)
        _electric_jobs[job_id] = {"status": "error", "error": str(exc)}
        return

    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    out_dir = electric_dir / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save debug CSV whenever there are station mismatches (regardless of output)
    debug_url: str | None = None
    if debug_csv:
        _electric_debug[run_id] = debug_csv
        (out_dir / "debug_mismatches.csv").write_text(debug_csv, encoding="utf-8")
        debug_url = f"/api/electric/debug/{run_id}"

    if not tss_outputs:
        error_msg = (
            "Pipeline produced no TSS outputs — all route segments have unresolved station names. "
            "Check that station names in your route CSV match those in station_points.csv "
            "(matching is case-insensitive but spelling must be exact)."
        )
        if debug_url:
            error_msg += f" Download the mismatch report: {debug_url}"
        # Save run metadata so the debug file is accessible from history
        (out_dir / "metadata.json").write_text(
            json.dumps({
                "run_id": run_id, "tss_files": [], "created_at": created_at,
                "debug_url": debug_url, "error": error_msg,
            }),
            encoding="utf-8",
        )
        _electric_jobs[job_id] = {
            "status": "error",
            "error": error_msg,
            "run_id": run_id,
            "debug_url": debug_url,
        }
        return

    _electric_runs[run_id] = tss_outputs
    tss_files = [f"{name}.csv" for name in tss_outputs]

    # Persist TSS outputs to disk
    for tss_name, csv_text in tss_outputs.items():
        (out_dir / f"{tss_name}.csv").write_text(csv_text, encoding="utf-8")
    (out_dir / "metadata.json").write_text(
        json.dumps({
            "run_id": run_id, "tss_files": tss_files, "created_at": created_at,
            "debug_url": debug_url,
        }),
        encoding="utf-8",
    )

    _electric_jobs[job_id] = {
        "status": "done",
        "run_id": run_id,
        "tss_files": tss_files,
        "created_at": created_at,
        "debug_url": debug_url,
    }
    logger.info("Electric job %s done — run %s: %d TSS outputs", job_id, run_id, len(tss_outputs))


@router.post("/run")
async def run_electric(req: RunRequest, request: Request) -> JSONResponse:
    """Start the energy pipeline in the background. Returns a job_id immediately (HTTP 202).

    Poll GET /api/electric/job/{job_id} until status='done' or 'error'.
    """
    state = request.app.state
    electric_dir = _get_electric_dir(state)

    rolling_stock_path = electric_dir / "rolling_stock.csv"
    station_points_path = electric_dir / "station_points.csv"

    if not rolling_stock_path.exists():
        raise HTTPException(status_code=400, detail="Rolling stock CSV not uploaded yet")
    if not station_points_path.exists():
        raise HTTPException(status_code=400, detail="Station points CSV not uploaded yet")

    timetable_parts: list[str] = []
    route_parts: list[str] = []

    if req.result_ids:
        for gen_id in req.result_ids:
            tt = state.result_store.load_csv(gen_id, "timetable")
            rt = state.result_store.load_csv(gen_id, "route")
            if tt is None or rt is None:
                raise HTTPException(status_code=404, detail=f"Result '{gen_id}' not found on disk")
            timetable_parts.append(tt)
            route_parts.append(rt)
    else:
        tt_path = electric_dir / "timetable.csv"
        rt_path = electric_dir / "route.csv"
        if not tt_path.exists():
            raise HTTPException(status_code=400, detail="No timetable.csv uploaded directly.")
        if not rt_path.exists():
            raise HTTPException(status_code=400, detail="No route.csv uploaded directly.")
        timetable_parts.append(tt_path.read_text(encoding="utf-8-sig"))
        route_parts.append(rt_path.read_text(encoding="utf-8-sig"))

    job_id = str(uuid.uuid4())
    _electric_jobs[job_id] = {"status": "processing"}

    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        _run_pipeline_job,
        job_id, timetable_parts, route_parts,
        rolling_stock_path, station_points_path,
        electric_dir, state,
    )

    logger.info("Electric job %s started", job_id)
    return JSONResponse(status_code=202, content={"job_id": job_id})


@router.get("/job/{job_id}")
async def get_electric_job(job_id: str) -> JSONResponse:
    """Poll the status of an electric pipeline job."""
    job = _electric_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return JSONResponse(content=job)


# ---------------------------------------------------------------------------
# Download output
# ---------------------------------------------------------------------------

@router.get("/output/{run_id}/{filename}")
async def get_electric_output(run_id: str, filename: str, request: Request) -> Response:
    """Download a per-TSS output CSV."""
    if not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="filename must end with .csv")

    tss_name = filename[:-4]
    run = _electric_runs.get(run_id)
    if run and tss_name in run:
        return Response(
            content=run[tss_name],
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    electric_dir = _get_electric_dir(request.app.state)
    path = electric_dir / "runs" / run_id / filename
    if path.exists():
        return Response(
            content=path.read_text(encoding="utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    raise HTTPException(status_code=404, detail=f"Output '{filename}' not found for run '{run_id}'")


@router.get("/debug/{run_id}")
async def get_electric_debug(run_id: str, request: Request) -> Response:
    """Download the station-mismatch debug CSV for a run (only present when mismatches occurred)."""
    if run_id in _electric_debug:
        return Response(
            content=_electric_debug[run_id],
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=electric_debug_mismatches.csv"},
        )
    electric_dir = _get_electric_dir(request.app.state)
    path = electric_dir / "runs" / run_id / "debug_mismatches.csv"
    if path.exists():
        return Response(
            content=path.read_text(encoding="utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=electric_debug_mismatches.csv"},
        )
    raise HTTPException(status_code=404, detail=f"No debug file for run '{run_id}'")


# ---------------------------------------------------------------------------
# Runs history
# ---------------------------------------------------------------------------

@router.get("/runs")
async def list_electric_runs(request: Request) -> JSONResponse:
    """Return metadata for all past Electric pipeline runs, newest first."""
    electric_dir = _get_electric_dir(request.app.state)
    runs_dir = electric_dir / "runs"
    if not runs_dir.exists():
        return JSONResponse(content=[])
    results = []
    for d in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        meta_path = d / "metadata.json"
        if meta_path.exists():
            try:
                results.append(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception:
                pass
    return JSONResponse(content=results)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

@router.post("/merge")
async def merge_results(req: MergeRequest, request: Request) -> JSONResponse:
    """Merge timetable + route CSVs from multiple stored results into a new stored result."""
    state = request.app.state

    if len(req.result_ids) < 2:
        raise HTTPException(status_code=400, detail="Provide at least 2 result IDs to merge")

    timetable_parts: list[str] = []
    route_parts: list[str] = []
    for gen_id in req.result_ids:
        tt = state.result_store.load_csv(gen_id, "timetable")
        rt = state.result_store.load_csv(gen_id, "route")
        if tt is None or rt is None:
            raise HTTPException(status_code=404, detail=f"Result '{gen_id}' not found on disk")
        timetable_parts.append(tt)
        route_parts.append(rt)

    try:
        combined_tt = combine_csvs(timetable_parts)
        combined_route = combine_route_csvs(route_parts)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Merge error: {exc}")

    tt_rows = len(pd.read_csv(io.StringIO(combined_tt))) if combined_tt.strip() else 0
    rt_rows = len(pd.read_csv(io.StringIO(combined_route))) if combined_route.strip() else 0

    gen_id = str(uuid.uuid4())
    metadata = {
        "station_name": f"Merged ({len(req.result_ids)} results)",
        "operator_code": "—",
        "date_start": "",
        "date_end": "",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timetable_rows": tt_rows,
        "route_rows": rt_rows,
        "merged_from": req.result_ids,
    }
    state.result_store.save(gen_id, combined_tt, combined_route, "", metadata)

    logger.info("Merged %d results → %s (%d timetable rows, %d route rows)",
                len(req.result_ids), gen_id, tt_rows, rt_rows)

    return JSONResponse(content={"gen_id": gen_id, "timetable_rows": tt_rows, "route_rows": rt_rows})
