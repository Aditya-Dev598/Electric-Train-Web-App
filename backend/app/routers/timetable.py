"""FastAPI router for timetable generation endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from backend.app.security.validators import (
    ValidationError,
    validate_date,
    validate_date_range,
    validate_operator_code,
    validate_route_name,
    validate_station_name,
)
from backend.app.services.csv_exporter import (
    generate_debug_csv,
    generate_route_csv,
    generate_timetable_csv,
)

router = APIRouter(prefix="/api", tags=["timetable"])

# In-memory result cache (keyed by generation ID)
# For production, use Redis or similar persistent store
_result_cache: dict[str, dict] = {}


class ValidateRequest(BaseModel):
    station_name: str = Field(..., min_length=1, max_length=100)
    operator_code: str = Field(..., min_length=2, max_length=3)
    date_start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_end: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    train_route: str = Field(..., min_length=1, max_length=200)
    route_variant: str = Field(..., min_length=1, max_length=200)


class GenerateRequest(BaseModel):
    station_name: str = Field(..., min_length=1, max_length=100)
    operator_code: str = Field(..., min_length=2, max_length=3)
    date_start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_end: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    train_route: str = Field(..., min_length=1, max_length=200)
    route_variant: str = Field(..., min_length=1, max_length=200)


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

    try:
        validate_route_name(req.train_route, "train_route")
    except ValidationError as e:
        errors.append({"field": e.field, "message": e.message})

    try:
        validate_route_name(req.route_variant, "route_variant")
    except ValidationError as e:
        errors.append({"field": e.field, "message": e.message})

    if errors:
        return JSONResponse(
            status_code=422,
            content={"valid": False, "errors": errors},
        )

    return JSONResponse(content={"valid": True, "errors": []})


@router.post("/generate")
async def generate_csv(req: GenerateRequest, request: Request) -> JSONResponse:
    """Generate timetable and route CSVs."""
    # Validate inputs
    try:
        station = validate_station_name(req.station_name)
        operator = validate_operator_code(req.operator_code)
        start_date = validate_date(req.date_start)
        end_date = (
            validate_date(req.date_end) if req.date_end
            else start_date
        )
        if req.date_end:
            validate_date_range(req.date_start, req.date_end)
        train_route = validate_route_name(req.train_route, "train_route")
        route_variant = validate_route_name(req.route_variant, "route_variant")
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"{e.field}: {e.message}")

    # Get orchestrator from app state
    orchestrator = request.app.state.orchestrator

    # Run generation
    result = orchestrator.generate(
        station_name=station,
        operator_code=operator,
        date_start=start_date,
        date_end=end_date,
        train_route=train_route,
        route_variant=route_variant,
    )

    # Generate CSVs
    timetable_csv = generate_timetable_csv(result.timetable_rows)
    route_csv = generate_route_csv(result.route_rows)
    debug_csv = generate_debug_csv(result.debug_rows)

    # Store in cache with unique ID
    gen_id = str(uuid.uuid4())
    _result_cache[gen_id] = {
        "timetable_csv": timetable_csv,
        "route_csv": route_csv,
        "debug_csv": debug_csv,
        "result": {
            "timetable_row_count": len(result.timetable_rows),
            "route_row_count": len(result.route_rows),
            "warnings": result.warnings,
            "provenance": result.provenance,
            "summary": result.summary,
        },
    }

    # Limit cache size
    if len(_result_cache) > 100:
        oldest = next(iter(_result_cache))
        del _result_cache[oldest]

    return JSONResponse(content={
        "generation_id": gen_id,
        "timetable_rows": len(result.timetable_rows),
        "route_rows": len(result.route_rows),
        "warnings": result.warnings,
        "provenance": result.provenance,
        "summary": result.summary,
        "timetable_preview": [
            {
                "date": r.date,
                "departure_time": r.departure_time,
                "train_route": r.train_route,
                "train_class": r.train_class,
                "number_of_coaches": r.number_of_coaches,
            }
            for r in result.timetable_rows[:20]  # Preview first 20
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
    })


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
