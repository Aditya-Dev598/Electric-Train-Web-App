"""FastAPI router for server-side CIF timetable management.

Upload flow (non-blocking):
  1. POST /api/cif/upload  — saves bytes to disk immediately, returns 202,
                             then parses in a background thread pool worker.
  2. GET  /api/cif/status  — returns { loaded, loading, schedule_count, … }
                             Poll this after upload until loading=false.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from backend.app.services.cif_parser import CIFParser

router = APIRouter(prefix="/api/cif", tags=["cif"])
logger = logging.getLogger(__name__)

_CIF_UPLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "cif"
_CIF_UPLOAD_PATH = _CIF_UPLOAD_DIR / "uploaded.CIF"

# 2 GB hard cap (checked before any processing)
_CIF_MAX_BYTES = 2 * 1024 * 1024 * 1024


def _parse_cif_background(state, raw_bytes: bytes, filename: str) -> None:
    """CPU-bound parse — runs in a thread-pool worker, never on the event loop."""
    try:
        text = raw_bytes.decode("utf-8", errors="replace")
        del raw_bytes  # free memory before building schedule objects

        temp_parser = CIFParser()
        # Use StringIO so parse_lines streams line-by-line (no splitlines() copy)
        temp_parser.parse_lines(io.StringIO(text), source=filename)
        del text

        if not temp_parser.schedules:
            logger.warning("CIF parse completed but no schedules found in '%s'", filename)
            state.cif_loading = False
            return

        state.cif_parser._schedules = temp_parser._schedules
        state.cif_parser._header_date = getattr(temp_parser, "_header_date", None)
        state.cif_filename = filename
        state.cif_schedule_count = len(temp_parser.schedules)
        state.cif_loaded_at = datetime.now(timezone.utc).isoformat()
        logger.info("CIF background parse done: %d schedules from '%s'",
                    len(temp_parser.schedules), filename)
    except Exception as exc:
        logger.exception("CIF background parse failed: %s", exc)
    finally:
        state.cif_loading = False


@router.post("/upload")
async def upload_cif(
    request: Request,
    cif_file: UploadFile = File(..., description="CIF/MCA timetable file"),
) -> JSONResponse:
    """Save the CIF file to disk and start parsing in the background.

    Returns 202 immediately — poll GET /api/cif/status until loading=false.
    """
    raw_bytes = await cif_file.read()
    filename = cif_file.filename or "upload.CIF"

    if len(raw_bytes) > _CIF_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CIF file too large ({len(raw_bytes) // (1024*1024)} MB). Maximum is 2 GB.",
        )

    # LFS pointer guard (fast check on first 200 bytes)
    first_line = raw_bytes[:200].decode("utf-8", errors="replace").split("\n", 1)[0].rstrip()
    if first_line.startswith("version https://git-lfs.github.com"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Uploaded file is a Git LFS pointer, not the actual CIF data. "
                "Run 'git lfs pull' to download the real file before uploading."
            ),
        )

    # Persist to disk immediately (fast sequential write)
    _CIF_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _CIF_UPLOAD_PATH.write_bytes(raw_bytes)

    # Mark as loading and kick off background parse (non-blocking)
    state = request.app.state
    state.cif_loading = True
    state.cif_filename = filename

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _parse_cif_background, state, raw_bytes, filename)

    logger.info("CIF file saved (%d MB) — parsing in background", len(raw_bytes) // (1024 * 1024))

    return JSONResponse(
        status_code=202,
        content={
            "loading": True,
            "filename": filename,
            "message": "File saved. Parsing in background — poll /api/cif/status until loading=false.",
        },
    )


@router.get("/status")
async def cif_status(request: Request) -> JSONResponse:
    """Return the status of the server-loaded CIF timetable."""
    state = request.app.state
    loading = getattr(state, "cif_loading", False)
    schedule_count = len(state.cif_parser.schedules) if state.cif_parser.schedules else 0

    return JSONResponse(content={
        "loaded": schedule_count > 0,
        "loading": loading,
        "filename": getattr(state, "cif_filename", None),
        "schedule_count": schedule_count,
        "loaded_at": getattr(state, "cif_loaded_at", None),
    })
