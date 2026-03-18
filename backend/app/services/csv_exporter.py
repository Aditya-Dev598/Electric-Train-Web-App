"""CSV exporter with injection protection and proper formatting.

Generates:
- Timetable CSV (route_variant, date, departure_time, train_route, train_class, number_of_coaches)
- Route CSV (route_variant, seq, from_station, to_station, distance_miles, run_min, wait_min)
- Debug CSV (service identifiers, matching results, missing field reasons)
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
    """Protect against CSV injection by prefixing dangerous characters.

    Spreadsheet applications (Excel, Google Sheets, LibreOffice) can execute
    formulas injected via CSV fields starting with =, +, -, @, tab, or CR.
    We prefix such values with a single quote to neutralize them.
    """
    if not value:
        return value
    if _INJECTION_PATTERN.match(value):
        return "'" + value
    return value


def _sanitize_row(row: dict[str, str]) -> dict[str, str]:
    """Sanitize all string values in a row dict."""
    return {k: sanitize_csv_value(str(v)) for k, v in row.items()}


def generate_timetable_csv(rows: list[TimetableRow]) -> str:
    """Generate the timetable CSV string.

    Fields: route_variant, date, departure_time, train_route, train_class, number_of_coaches
    """
    output = io.StringIO()
    fieldnames = ["route_variant", "date", "departure_time", "train_route",
                  "train_class", "number_of_coaches"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()

    for row in rows:
        writer.writerow(_sanitize_row({
            "route_variant": row.route_variant,
            "date": row.date,
            "departure_time": row.departure_time,
            "train_route": row.train_route,
            "train_class": row.train_class,
            "number_of_coaches": row.number_of_coaches,
        }))

    return output.getvalue()


def generate_route_csv(rows: list[RouteRow]) -> str:
    """Generate the route CSV string.

    Fields: route_variant, seq, from_station, to_station, distance_miles, run_min, wait_min
    """
    output = io.StringIO()
    fieldnames = ["route_variant", "seq", "from_station", "to_station",
                  "distance_miles", "run_min", "wait_min"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()

    for row in rows:
        writer.writerow(_sanitize_row({
            "route_variant": row.route_variant,
            "seq": str(row.seq),
            "from_station": row.from_station,
            "to_station": row.to_station,
            "distance_miles": row.distance_miles,
            "run_min": row.run_min,
            "wait_min": row.wait_min,
        }))

    return output.getvalue()


def generate_debug_csv(rows: list[DebugRow]) -> str:
    """Generate the debug/diagnostic CSV string."""
    output = io.StringIO()
    fieldnames = [
        "service_date", "train_uid", "stp_indicator", "headcode",
        "origin_tiploc", "origin_departure", "darwin_rid",
        "matching_method", "confidence", "train_class",
        "number_of_coaches", "failure_reason", "mileage_status",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()

    for row in rows:
        writer.writerow(_sanitize_row({
            "service_date": row.service_date,
            "train_uid": row.train_uid,
            "stp_indicator": row.stp_indicator,
            "headcode": row.headcode,
            "origin_tiploc": row.origin_tiploc,
            "origin_departure": row.origin_departure,
            "darwin_rid": row.darwin_rid,
            "matching_method": row.matching_method,
            "confidence": row.confidence,
            "train_class": row.train_class,
            "number_of_coaches": row.number_of_coaches,
            "failure_reason": row.failure_reason,
            "mileage_status": row.mileage_status,
        }))

    return output.getvalue()
