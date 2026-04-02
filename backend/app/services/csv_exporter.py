"""CSV exporter with injection protection and proper formatting.

Generates:
- Timetable CSV (route_variant, stop_type, date, departure_time, train_class, number_of_coaches)
- Route CSV (route_variant, seq, from_station, to_station, stop_type,
             distance_miles, avg_elevation_m, run_min, wait_min)
- Debug CSV (service identifiers, matching results, CIF BS fields, missing field reasons)

Numeric fields (distance_miles, avg_elevation_m, run_min, wait_min, number_of_coaches,
seq) are written as bare numbers — not quoted strings — so spreadsheet applications
and downstream parsers see them as the correct type. None values are written as "".
"""

from __future__ import annotations

import csv
import io
import re
from typing import Union

from backend.app.models import DebugRow, GenerationResult, RouteRow, TimetableRow


# Characters that could trigger formula injection in spreadsheet applications
_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
_INJECTION_PATTERN = re.compile(r"^[=+\-@\t\r\n]")


def sanitize_csv_value(value: str) -> str:
    """Protect against CSV injection by prefixing dangerous characters."""
    if not value:
        return value
    if _INJECTION_PATTERN.match(value):
        return "'" + value
    return value


def _n(value: object) -> object:
    """Return numeric value as-is, or '' for None. Strings are sanitized."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    return sanitize_csv_value(str(value))


def generate_timetable_csv(rows: list[TimetableRow]) -> str:
    """Generate the timetable CSV string."""
    output = io.StringIO()
    fieldnames = ["route_variant", "stop_type", "date", "departure_time",
                  "train_class", "number_of_coaches"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()

    for row in rows:
        writer.writerow({
            "route_variant": sanitize_csv_value(row.route_variant),
            "stop_type": sanitize_csv_value(row.stop_type),
            "date": sanitize_csv_value(row.date),
            "departure_time": sanitize_csv_value(row.departure_time),
            "train_class": sanitize_csv_value(row.train_class),
            "number_of_coaches": _n(row.number_of_coaches),
        })

    return output.getvalue()


def generate_route_csv(rows: list[RouteRow]) -> str:
    """Generate the route CSV string.

    Numeric fields (seq, distance_miles, avg_elevation_m, run_min, wait_min)
    are written as bare numbers; None → empty string.
    """
    output = io.StringIO()
    fieldnames = ["route_variant", "seq", "from_station", "to_station", "stop_type",
                  "distance_miles", "avg_elevation_m", "run_min", "wait_min"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()

    for row in rows:
        writer.writerow({
            "route_variant": sanitize_csv_value(row.route_variant),
            "seq": _n(row.seq),
            "from_station": sanitize_csv_value(row.from_station),
            "to_station": sanitize_csv_value(row.to_station),
            "stop_type": sanitize_csv_value(row.stop_type),
            "distance_miles": _n(row.distance_miles),
            "avg_elevation_m": _n(row.avg_elevation_m),
            "run_min": _n(row.run_min),
            "wait_min": _n(row.wait_min),
        })

    return output.getvalue()


def generate_debug_csv(rows: list[DebugRow]) -> str:
    """Generate the debug/diagnostic CSV string."""
    output = io.StringIO()
    fieldnames = [
        "service_date", "train_uid", "stp_indicator", "headcode",
        "origin_tiploc", "origin_departure", "darwin_rid",
        "matching_method", "confidence", "train_class",
        "number_of_coaches", "failure_reason", "mileage_status",
        "power_type", "timing_load", "seating_class",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()

    for row in rows:
        writer.writerow({
            "service_date": sanitize_csv_value(row.service_date),
            "train_uid": sanitize_csv_value(row.train_uid),
            "stp_indicator": sanitize_csv_value(row.stp_indicator),
            "headcode": sanitize_csv_value(row.headcode),
            "origin_tiploc": sanitize_csv_value(row.origin_tiploc),
            "origin_departure": sanitize_csv_value(row.origin_departure),
            "darwin_rid": sanitize_csv_value(row.darwin_rid),
            "matching_method": sanitize_csv_value(row.matching_method),
            "confidence": sanitize_csv_value(row.confidence),
            "train_class": sanitize_csv_value(row.train_class),
            "number_of_coaches": sanitize_csv_value(row.number_of_coaches),
            "failure_reason": sanitize_csv_value(row.failure_reason),
            "mileage_status": sanitize_csv_value(row.mileage_status),
            "power_type": sanitize_csv_value(row.power_type),
            "timing_load": sanitize_csv_value(row.timing_load),
            "seating_class": sanitize_csv_value(row.seating_class),
        })

    return output.getvalue()
