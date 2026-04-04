"""Schedule validator: apply date ranges, day-of-week masks, and STP overlay logic.

Implements the CIF STP (Short Term Plan) overlay priority system:
- P = Permanent schedule (base timetable)
- O = Overlay (replaces P for specific dates)
- C = Cancellation (cancels P or O for specific dates)
- N = New/Extra (additional service on specific dates)

Priority: C > O > N > P (for same UID on same date)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from backend.app.models import CIFSchedule, STPIndicator

logger = logging.getLogger(__name__)

# Monday=0 ... Sunday=6 (Python weekday convention)
# CIF days_run: index 0=Monday, 1=Tuesday, ... 6=Sunday
DAY_INDEX_MAP = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}


def schedule_runs_on_date(schedule: CIFSchedule, target_date: date) -> bool:
    """Check if a schedule is valid for a specific date.

    Checks:
    1. Date falls within date_runs_from..date_runs_to (inclusive)
    2. Day-of-week mask matches
    """
    if target_date < schedule.date_runs_from:
        return False
    if target_date > schedule.date_runs_to:
        return False

    # Check day-of-week mask
    weekday = target_date.weekday()  # 0=Monday
    if weekday < len(schedule.days_run) and schedule.days_run[weekday] != "1":
        return False

    return True


def apply_stp_overlays(
    schedules: list[CIFSchedule],
    target_date: date,
) -> list[CIFSchedule]:
    """Apply STP overlay logic to resolve effective schedules for a date.

    Groups schedules by Train UID, then for each UID:
    1. Find all schedules valid on target_date
    2. Apply STP priority: C > O > N > P
    3. If C exists → schedule is cancelled (excluded)
    4. If O exists → O replaces P
    5. If N exists → N is additional (included alongside)
    6. If only P → use P

    Returns the list of effective schedules for the given date.
    """
    # Group by Train UID
    by_uid: dict[str, list[CIFSchedule]] = defaultdict(list)
    for sched in schedules:
        if schedule_runs_on_date(sched, target_date):
            by_uid[sched.train_uid].append(sched)

    effective: list[CIFSchedule] = []

    for uid, uid_schedules in by_uid.items():
        # Separate by STP type
        cancellations = [s for s in uid_schedules if s.stp_indicator == STPIndicator.CANCELLATION]
        overlays = [s for s in uid_schedules if s.stp_indicator == STPIndicator.OVERLAY]
        new_extras = [s for s in uid_schedules if s.stp_indicator == STPIndicator.NEW]
        permanents = [s for s in uid_schedules if s.stp_indicator == STPIndicator.PERMANENT]

        # If any cancellation exists for this UID on this date, skip entirely
        if cancellations:
            logger.debug("UID %s cancelled on %s by STP C", uid, target_date)
            continue

        # If overlay exists, use overlay instead of permanent
        if overlays:
            effective.extend(overlays)
            logger.debug("UID %s overlaid on %s by STP O", uid, target_date)
        elif permanents:
            effective.extend(permanents)

        # N (new/extra) services are always additional
        effective.extend(new_extras)

    return effective


def filter_schedules(
    schedules: list[CIFSchedule],
    operator_code: Optional[str] = None,
    station_tiploc: Optional[str] = None,
    station_tiplocs: Optional[list[str]] = None,
) -> list[CIFSchedule]:
    """Filter schedules by operator code and/or station TIPLOC(s).

    Operator filtering uses the ATOC code from the BX record.
    Station filtering checks if any location in the schedule matches any of the
    given TIPLOCs. Pass station_tiplocs (list) to match across multiple TIPLOCs
    for the same station (e.g. Waterloo has WATRLOO, WATRLMN, etc.).
    station_tiploc (single string) is kept for backward compatibility.
    """
    result = schedules

    if operator_code:
        op = operator_code.strip().upper()
        result = [s for s in result if s.atoc_code.upper() == op]

    # Merge single and list args into one set
    tiploc_set: set[str] = set()
    if station_tiploc:
        tiploc_set.add(station_tiploc.strip().upper())
    if station_tiplocs:
        tiploc_set.update(t.strip().upper() for t in station_tiplocs)

    if tiploc_set:
        result = [
            s for s in result
            if any(loc.tiploc.upper() in tiploc_set for loc in s.locations)
        ]

    return result


def get_departure_at_station(
    schedule: CIFSchedule,
    station_tiploc: str | list[str],
) -> Optional[str]:
    """Get the departure time at a specific station for a schedule.

    station_tiploc can be a single TIPLOC string or a list of TIPLOCs
    (for stations with multiple CIF entries, e.g. Waterloo).
    Returns the public or scheduled departure time as HHMM string,
    or None if the station is not found or has no departure (terminus).
    """
    if isinstance(station_tiploc, list):
        tiploc_set = {t.strip().upper() for t in station_tiploc}
    else:
        tiploc_set = {station_tiploc.strip().upper()}
    for loc in schedule.locations:
        if loc.tiploc.upper() in tiploc_set:
            dep = loc.departure_time_str
            if dep:
                return dep
    return None


def get_stop_type_at_station(
    schedule: CIFSchedule,
    station_tiploc: str | list[str],
) -> Optional[str]:
    """Return 'stop' or 'pass' for a station in a schedule, or None if not found.

    station_tiploc can be a single TIPLOC string or a list of TIPLOCs.
    'stop'  — the train calls at this station (LO/LT, or LI with public times/activity).
    'pass'  — the train passes through without stopping (LI with only a pass time).
    """
    if isinstance(station_tiploc, list):
        tiploc_set = {t.strip().upper() for t in station_tiploc}
    else:
        tiploc_set = {station_tiploc.strip().upper()}
    for loc in schedule.locations:
        if loc.tiploc.upper() in tiploc_set:
            return "stop" if loc.is_passenger_stop else "pass"
    return None


def expand_date_range(start: date, end: date) -> list[date]:
    """Expand a date range into individual dates (inclusive)."""
    dates = []
    current = start
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)
    return dates
