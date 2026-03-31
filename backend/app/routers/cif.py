"""FastAPI router for server-side CIF timetable management.

Allows users to upload a CIF/MCA file once and reuse it across all generate requests.
The uploaded file is persisted to disk and reloaded into the shared CIFParser instance.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from backend.app.services.cif_parser import CIFParser

router = APIRouter(prefix="/api/cif", tags=["cif"])
logger = logging.getLogger(__name__)

# Where the uploaded CIF file is persisted on disk
_CIF_UPLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "cif"
_CIF_UPLOAD_PATH = _CIF_UPLOAD_DIR / "uploaded.CIF"

# Maximum CIF upload size: 2 GB (real CIF files are ~1 GB)
_CIF_MAX_BYTES = 2 * 1024 * 1024 * 1024


@router.post("/upload")
async def upload_cif(
    request: Request,
    cif_file: UploadFile = File(..., description="CIF/MCA timetable file from Network Rail"),
) -> JSONResponse:
    """Upload a CIF timetable file and reload it into the server's CIF parser.

    Once uploaded, subsequent /api/generate calls can omit the CIF file and
    the server-loaded timetable will be used automatically.
    """
    raw_bytes = await cif_file.read()
    filename = cif_file.filename or "upload.CIF"

    # Size guard (multipart is exempt from the global RequestSizeLimitMiddleware)
    if len(raw_bytes) > _CIF_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CIF file too large ({len(raw_bytes) // (1024*1024)} MB). Maximum is 2 GB.",
        )

    # LFS pointer guard
    first_line = raw_bytes[:200].decode("utf-8", errors="replace").split("\n", 1)[0].rstrip()
    if first_line.startswith("version https://git-lfs.github.com"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Uploaded file is a Git LFS pointer, not the actual CIF data. "
                "Run 'git lfs pull' to download the real file before uploading."
            ),
        )

    # Parse into a temporary parser to validate before committing
    text = raw_bytes.decode("utf-8", errors="replace")
    del raw_bytes

    temp_parser = CIFParser()
    temp_parser.parse_lines(text.splitlines(), source=filename)

    if not temp_parser.schedules:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No CIF schedules found in '{filename}'. "
                "Ensure the file is a valid Network Rail CIF/MCA timetable."
            ),
        )

    # Persist to disk
    _CIF_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _CIF_UPLOAD_PATH.write_text(text, encoding="utf-8")
    del text

    # Reload the shared CIF parser instance
    state = request.app.state
    state.cif_parser._schedules = temp_parser._schedules
    state.cif_parser._header = getattr(temp_parser, "_header", None)

    # Store metadata for /status
    state.cif_filename = filename
    state.cif_schedule_count = len(temp_parser.schedules)
    state.cif_loaded_at = datetime.now(timezone.utc).isoformat()

    logger.info("Server CIF updated: %s (%d schedules)", filename, len(temp_parser.schedules))

    return JSONResponse(content={
        "loaded": True,
        "filename": filename,
        "schedule_count": len(temp_parser.schedules),
        "loaded_at": state.cif_loaded_at,
    })


@router.get("/status")
async def cif_status(request: Request) -> JSONResponse:
    """Return the status of the server-loaded CIF timetable."""
    state = request.app.state
    schedule_count = len(state.cif_parser.schedules) if state.cif_parser.schedules else 0

    if schedule_count == 0:
        return JSONResponse(content={
            "loaded": False,
            "filename": None,
            "schedule_count": 0,
            "loaded_at": None,
        })

    return JSONResponse(content={
        "loaded": True,
        "filename": getattr(state, "cif_filename", "server CIF"),
        "schedule_count": schedule_count,
        "loaded_at": getattr(state, "cif_loaded_at", None),
    })
