"""Route builder: extract unique stopping patterns and build route definitions.

Separates timetable instances (per date/service) from route patterns (reusable).
Groups identical station sequences into one route definition.
Only includes passenger stations (those with CRS codes in CORPUS).
"""

from __future__ import annotations

import logging
from typing import Optional

from backend.app.models import CIFSchedule, RouteRow
from backend.app.services.corpus_mapper import CorpusMapper
from backend.app.services.mileage_resolver import MileageResolver
from backend.app.services.audit_logger import AuditLogger
from backend.app.utils.time_utils import (
    parse_cif_time,
    calculate_run_minutes,
    calculate_wait_minutes,
)

logger = logging.getLogger(__name__)


def _get_station_display(tiploc: str, corpus: CorpusMapper) -> str:
    """Get the best display name for a station: CRS if available, else TIPLOC."""
    crs = corpus.tiploc_to_crs(tiploc)
    return crs if crs else tiploc


def extract_stopping_pattern(
    schedule: CIFSchedule,
    corpus: CorpusMapper,
) -> tuple[str, ...]:
    """Extract the ordered tuple of passenger station CRS codes for a schedule.

    Only includes stations that are passenger stops (have CRS codes).
    Returns a tuple suitable for use as a dict key for deduplication.
    """
    pattern = []
    for loc in schedule.passenger_stops:
        tiploc = loc.tiploc.upper()
        if corpus.is_passenger_station(tiploc):
            crs = corpus.tiploc_to_crs(tiploc) or tiploc
            pattern.append(crs)
    return tuple(pattern)


def identify_unique_routes(
    schedules: list[CIFSchedule],
    corpus: CorpusMapper,
) -> dict[tuple[str, ...], CIFSchedule]:
    """Identify unique stopping patterns from a list of schedules.

    Returns a dict mapping pattern tuples to a representative schedule
    (the first schedule found with that pattern).
    """
    unique: dict[tuple[str, ...], CIFSchedule] = {}

    for schedule in schedules:
        pattern = extract_stopping_pattern(schedule, corpus)
        if len(pattern) >= 2 and pattern not in unique:
            unique[pattern] = schedule

    logger.info("Identified %d unique route patterns from %d schedules",
                len(unique), len(schedules))
    return unique


def build_route_rows(
    schedule: CIFSchedule,
    route_variant: str,
    corpus: CorpusMapper,
    mileage: MileageResolver,
    audit: AuditLogger,
) -> list[RouteRow]:
    """Build route CSV rows for a single schedule's stopping pattern.

    Creates ordered station pairs with:
    - seq (1..n)
    - from_station, to_station (CRS codes)
    - distance_miles (from official mileage data)
    - run_min (departure A to arrival B)
    - wait_min (dwell time at from_station)
    """
    passenger_stops = [
        loc for loc in schedule.passenger_stops
        if corpus.is_passenger_station(loc.tiploc.upper())
    ]

    if len(passenger_stops) < 2:
        return []

    rows: list[RouteRow] = []

    for i in range(len(passenger_stops) - 1):
        from_loc = passenger_stops[i]
        to_loc = passenger_stops[i + 1]

        from_tiploc = from_loc.tiploc.upper()
        to_tiploc = to_loc.tiploc.upper()

        from_station = _get_station_display(from_tiploc, corpus)
        to_station = _get_station_display(to_tiploc, corpus)

        # Distance
        dist, method = mileage.get_distance_with_method(from_tiploc, to_tiploc)
        if dist is None:
            audit.log_mileage_resolution(from_station, to_station, None, False, method)
            distance_str = ""
        else:
            audit.log_mileage_resolution(from_station, to_station, dist, True, method)
            distance_str = f"{dist:.2f}"

        # Run time: departure from A to arrival at B
        dep_a = parse_cif_time(from_loc.departure_time_str)
        arr_b = parse_cif_time(to_loc.arrival_time_str)
        run = calculate_run_minutes(dep_a, arr_b)
        run_str = str(run) if run is not None else ""

        # Wait time: dwell at from_station (departure - arrival)
        arr_a = parse_cif_time(from_loc.arrival_time_str)
        dep_a_full = parse_cif_time(from_loc.departure_time_str)
        wait = calculate_wait_minutes(arr_a, dep_a_full)
        wait_str = str(wait)

        rows.append(RouteRow(
            route_variant=route_variant,
            seq=i + 1,
            from_station=from_station,
            to_station=to_station,
            distance_miles=distance_str,
            run_min=run_str,
            wait_min=wait_str,
        ))

    return rows
