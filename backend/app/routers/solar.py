"""FastAPI router for the Solar analysis pipeline.

Workflow:
  1. POST /api/solar/run  — upload demand CSV + PVGIS CSV; run pipeline; returns run_id
  2. GET  /api/solar/output/{run_id}/{filename}  — download any of the 5 output files
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from backend.app.services.solar_pipeline import run_solar_pipeline

router = APIRouter(prefix="/api/solar", tags=["solar"])
logger = logging.getLogger(__name__)

# In-memory store of completed solar runs { run_id: { filename: bytes } }
_solar_runs: dict[str, dict[str, bytes]] = {}

_OUTPUT_FILES = {
    "demand_hourly.xlsx",
    "pvgis_supply.xlsx",
    "avg_profile.xlsx",
    "avg_profile.png",
    "solar_metrics.xlsx",
}


def _get_solar_dir(state) -> Path:
    return Path(state.settings.solar_data_path)


@router.post("/run")
async def run_solar(
    request: Request,
    demand_csv: UploadFile = File(..., description="Half-hour TSS demand CSV (output from Electric pipeline)"),
    pvgis_csv: UploadFile = File(..., description="PVGIS hourly radiation CSV"),
) -> JSONResponse:
    """Run the solar analysis pipeline and return a run_id for downloading outputs."""
    demand_bytes = await demand_csv.read()
    pvgis_bytes = await pvgis_csv.read()

    if not demand_bytes:
        raise HTTPException(status_code=400, detail="demand_csv file is empty")
    if not pvgis_bytes:
        raise HTTPException(status_code=400, detail="pvgis_csv file is empty")

    demand_text = demand_bytes.decode("utf-8", errors="replace")
    pvgis_text = pvgis_bytes.decode("utf-8", errors="replace")

    try:
        result = run_solar_pipeline(demand_text, pvgis_text)
    except Exception as exc:
        logger.exception("Solar pipeline error")
        raise HTTPException(status_code=422, detail=str(exc))

    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    avg_png_b64 = base64.b64encode(result.avg_profile_png).decode()

    outputs: dict[str, bytes] = {
        "demand_hourly.xlsx": result.demand_hourly_xlsx,
        "pvgis_supply.xlsx": result.pvgis_supply_xlsx,
        "avg_profile.xlsx": result.avg_profile_xlsx,
        "avg_profile.png": result.avg_profile_png,
        "solar_metrics.xlsx": result.metrics_xlsx,
    }

    # Keep in memory
    _solar_runs[run_id] = outputs

    # Persist to disk
    solar_dir = _get_solar_dir(request.app.state)
    out_dir = solar_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    for fname, data in outputs.items():
        (out_dir / fname).write_bytes(data)
    (out_dir / "metadata.json").write_text(
        json.dumps({
            "run_id": run_id,
            "solar_share_pct": result.solar_share_pct,
            "utilisation_pct": result.utilisation_pct,
            "files": list(outputs.keys()),
            "avg_profile_png_b64": avg_png_b64,
            "created_at": created_at,
        }),
        encoding="utf-8",
    )

    logger.info("Solar run %s complete: share=%.1f%% util=%.1f%%",
                run_id, result.solar_share_pct, result.utilisation_pct)

    return JSONResponse(content={
        "run_id": run_id,
        "solar_share_pct": result.solar_share_pct,
        "utilisation_pct": result.utilisation_pct,
        "files": list(outputs.keys()),
        "avg_profile_png_b64": avg_png_b64,
        "created_at": created_at,
    })


@router.get("/runs")
async def list_solar_runs(request: Request) -> JSONResponse:
    """Return metadata for all past Solar pipeline runs, newest first."""
    solar_dir = _get_solar_dir(request.app.state)
    if not solar_dir.exists():
        return JSONResponse(content=[])
    results = []
    for d in sorted(solar_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        meta_path = d / "metadata.json"
        if meta_path.exists():
            try:
                results.append(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception:
                pass
    return JSONResponse(content=results)


@router.get("/output/{run_id}/{filename}")
async def get_solar_output(run_id: str, filename: str, request: Request) -> Response:
    """Download a solar pipeline output file."""
    if filename not in _OUTPUT_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown filename '{filename}'. Valid: {sorted(_OUTPUT_FILES)}",
        )

    # Try memory first
    run = _solar_runs.get(run_id)
    if run and filename in run:
        data = run[filename]
    else:
        # Try disk
        path = _get_solar_dir(request.app.state) / run_id / filename
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Output '{filename}' not found for run '{run_id}'")
        data = path.read_bytes()

    media_type = "image/png" if filename.endswith(".png") else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
