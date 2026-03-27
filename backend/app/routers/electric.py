"""FastAPI router for the Electric Train energy pipeline.

Workflow:
  1. POST /api/electric/upload/{file_type}  — upload rolling_stock or station_points CSV
  2. GET  /api/electric/status              — check which reference files are loaded
  3. POST /api/electric/run                 — combine selected results, run pipeline
  4. GET  /api/electric/output/{run_id}/{filename} — download a per-TSS output CSV
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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

_executor = ThreadPoolExecutor(max_workers=1)

# In-memory store for completed electric run outputs
# { run_id: { tss_name: csv_str } }
_electric_runs: dict[str, dict[str, str]] = {}

_ALLOWED_FILE_TYPES = {"rolling_stock", "station_points", "tss_points"}


class RunRequest(BaseModel):
    result_ids: list[str]


def _get_electric_dir(state) -> Path:
    return Path(state.settings.electric_data_path)


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


@router.get("/status")
async def electric_status(request: Request) -> JSONResponse:
    """Return which Electric pipeline reference files are currently uploaded."""
    electric_dir = _get_electric_dir(request.app.state)
    return JSONResponse(content={
        "rolling_stock": (electric_dir / "rolling_stock.csv").exists(),
        "station_points": (electric_dir / "station_points.csv").exists(),
        "tss_points": (electric_dir / "tss_points.csv").exists(),
    })


@router.post("/run")
async def run_electric(req: RunRequest, request: Request) -> JSONResponse:
    """Combine selected timetable/route results and run the energy pipeline."""
    state = request.app.state
    electric_dir = _get_electric_dir(state)

    rolling_stock_path = electric_dir / "rolling_stock.csv"
    station_points_path = electric_dir / "station_points.csv"

    if not rolling_stock_path.exists():
        raise HTTPException(status_code=400, detail="Rolling stock CSV not uploaded yet")
    if not station_points_path.exists():
        raise HTTPException(status_code=400, detail="Station points CSV not uploaded yet")

    if not req.result_ids:
        raise HTTPException(status_code=400, detail="No result IDs provided")

    # Load and combine timetable + route CSVs from selected results
    timetable_parts: list[str] = []
    route_parts: list[str] = []
    for gen_id in req.result_ids:
        tt = state.result_store.load_csv(gen_id, "timetable")
        rt = state.result_store.load_csv(gen_id, "route")
        if tt is None or rt is None:
            raise HTTPException(status_code=404, detail=f"Result '{gen_id}' not found on disk")
        timetable_parts.append(tt)
        route_parts.append(rt)

    combined_tt = combine_csvs(timetable_parts)
    combined_route = combine_route_csvs(route_parts)

    # Run pipeline (CPU-bound but typically fast)
    try:
        tss_outputs = run_pipeline(
            timetable_csv=combined_tt,
            route_csv=combined_route,
            rolling_stock_path=rolling_stock_path,
            station_points_path=station_points_path,
        )
    except Exception as exc:
        logger.exception("Electric pipeline error")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}")

    if not tss_outputs:
        raise HTTPException(
            status_code=422,
            detail=(
                "Pipeline produced no outputs. Check that route_variant values in the timetable "
                "match those in the route CSV, and that station names match the station_points CSV."
            ),
        )

    run_id = str(uuid.uuid4())
    _electric_runs[run_id] = tss_outputs

    # Persist to disk as well
    out_dir = electric_dir / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for tss_name, csv_text in tss_outputs.items():
        (out_dir / f"{tss_name}.csv").write_text(csv_text, encoding="utf-8")

    logger.info("Electric run %s: %d TSS outputs", run_id, len(tss_outputs))

    return JSONResponse(content={
        "run_id": run_id,
        "tss_files": [f"{name}.csv" for name in tss_outputs],
    })


@router.get("/output/{run_id}/{filename}")
async def get_electric_output(run_id: str, filename: str, request: Request) -> Response:
    """Download a per-TSS output CSV."""
    if not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="filename must end with .csv")

    tss_name = filename[:-4]  # strip .csv

    # Try memory first
    run = _electric_runs.get(run_id)
    if run and tss_name in run:
        return Response(
            content=run[tss_name],
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    # Try disk
    electric_dir = _get_electric_dir(request.app.state)
    path = electric_dir / "runs" / run_id / filename
    if path.exists():
        return Response(
            content=path.read_text(encoding="utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    raise HTTPException(status_code=404, detail=f"Output '{filename}' not found for run '{run_id}'")
