"""Time parsing and calculation utilities for CIF timetable data."""

from __future__ import annotations

from typing import Optional


def parse_cif_time(raw: Optional[str]) -> Optional[int]:
    """Parse a CIF time string (HHMM or HHMMS) into total minutes since midnight.

    CIF uses HHMM for whole minutes and HHMMS where S='H' for half-minutes.
    Times can exceed 24:00 for services crossing midnight (e.g., '2530' = 01:30 next day).

    Returns total minutes since midnight of the operating day, or None if invalid.
    """
    if not raw or not raw.strip():
        return None

    cleaned = raw.strip()

    # Handle half-minute indicator (last char 'H' means +30 seconds, round up)
    half = False
    if cleaned.endswith("H"):
        half = True
        cleaned = cleaned[:-1]

    if len(cleaned) != 4 or not cleaned.isdigit():
        return None

    hours = int(cleaned[:2])
    minutes = int(cleaned[2:4])

    total = hours * 60 + minutes
    if half:
        total += 1  # Round up half-minute

    return total


def minutes_to_hhmmss(total_minutes: Optional[int]) -> str:
    """Convert total minutes since midnight to HH:MM:SS format.

    Handles times > 24:00 (midnight crossing) by wrapping.
    """
    if total_minutes is None:
        return ""

    # Normalize to 0-1439 range for display
    normalized = total_minutes % 1440
    hours = normalized // 60
    mins = normalized % 60
    return f"{hours:02d}:{mins:02d}:00"


def calculate_run_minutes(dep_minutes: Optional[int], arr_minutes: Optional[int]) -> Optional[int]:
    """Calculate running time in minutes between departure at A and arrival at B.

    Correctly handles midnight crossing (arrival < departure means next day).
    """
    if dep_minutes is None or arr_minutes is None:
        return None

    diff = arr_minutes - dep_minutes

    # If negative, service crosses midnight
    if diff < 0:
        diff += 1440  # Add 24 hours

    return diff


def calculate_wait_minutes(arr_minutes: Optional[int], dep_minutes: Optional[int]) -> int:
    """Calculate dwell/wait time at a station (departure - arrival at same station).

    Returns 0 for origin/pass-through or when times are unavailable.
    """
    if arr_minutes is None or dep_minutes is None:
        return 0

    diff = dep_minutes - arr_minutes

    # If negative, unlikely but handle midnight edge case
    if diff < 0:
        diff += 1440

    return diff
