"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health(request: Request) -> JSONResponse:
    """Health check endpoint returning service status and data availability."""
    status = {
        "status": "ok",
        "corpus_loaded": False,
        "cif_loaded": False,
        "mileage_loaded": False,
        "darwin_enabled": False,
    }

    if hasattr(request.app.state, "corpus"):
        status["corpus_loaded"] = request.app.state.corpus.is_loaded
    if hasattr(request.app.state, "cif_parser"):
        status["cif_loaded"] = len(request.app.state.cif_parser.schedules) > 0
    if hasattr(request.app.state, "mileage"):
        status["mileage_loaded"] = request.app.state.mileage.is_loaded
    if hasattr(request.app.state, "darwin"):
        status["darwin_enabled"] = request.app.state.darwin.is_enabled

    return JSONResponse(content=status)
