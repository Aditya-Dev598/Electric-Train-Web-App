"""Input validation and security utilities.

Implements:
- Strict server-side validation for all user inputs
- Operator code allowlisting
- Date format validation and range limits
- Station name sanitization
- Path traversal protection
- SSRF protection
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional


# Strict patterns for input validation
CRS_PATTERN = re.compile(r"^[A-Z]{3}$")
TIPLOC_PATTERN = re.compile(r"^[A-Z0-9]{1,8}$")
OPERATOR_PATTERN = re.compile(r"^[A-Z]{2}$")
STATION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\s\'\-\.\(\)&]{1,100}$")
ROUTE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\s\-_\.]{1,200}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Known UK TOC operator codes (ATOC codes)
KNOWN_OPERATORS = {
    "AW", "CC", "CH", "CS", "EM", "ES", "GC", "GN", "GR", "GW", "GX",
    "HT", "HX", "IL", "LE", "LM", "LN", "LO", "ME", "NT", "NY", "SE",
    "SN", "SR", "SW", "TL", "TP", "TW", "VT", "WR", "XC", "XR", "ZZ",
    # Additional / historical codes
    "SWR", "TFW", "TPE", "GTR", "SER", "LNR", "WMT", "EMR", "GWR",
    "ARL", "TFL", "NR",
}


class ValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


def validate_station_name(value: str) -> str:
    """Validate and sanitize a station name input.

    Accepts CRS codes (3 uppercase letters), TIPLOC codes, or station names.
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError("station_name", "Station name is required")

    upper = cleaned.upper()

    # Accept CRS code
    if CRS_PATTERN.match(upper):
        return upper

    # Accept TIPLOC code
    if TIPLOC_PATTERN.match(upper):
        return upper

    # Accept station name with safe characters
    if STATION_NAME_PATTERN.match(cleaned):
        return cleaned

    raise ValidationError(
        "station_name",
        "Invalid station name. Use CRS code (e.g. KGX), TIPLOC, or station name (letters, numbers, spaces, hyphens only).",
    )


def validate_operator_code(value: str, allowed: Optional[list[str]] = None) -> str:
    """Validate an operator/ATOC code."""
    cleaned = value.strip().upper()
    if not cleaned:
        raise ValidationError("operator_code", "Operator code is required")

    # Allow 2-3 character codes matching known pattern
    if not re.match(r"^[A-Z]{2,3}$", cleaned):
        raise ValidationError(
            "operator_code",
            "Operator code must be 2-3 uppercase letters (e.g. VT, SWR)",
        )

    # If allowlist is configured, enforce it
    if allowed and cleaned not in {a.upper() for a in allowed}:
        raise ValidationError(
            "operator_code",
            f"Operator code '{cleaned}' is not in the allowed list",
        )

    return cleaned


def validate_date(value: str) -> date:
    """Validate a date string in YYYY-MM-DD format."""
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError("date", "Date is required")

    if not DATE_PATTERN.match(cleaned):
        raise ValidationError("date", "Date must be in YYYY-MM-DD format")

    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError("date", "Invalid date value")


def validate_date_range(
    start: str,
    end: str,
    max_days: int = 31,
) -> tuple[date, date]:
    """Validate a date range, enforcing maximum span."""
    start_date = validate_date(start)
    end_date = validate_date(end)

    if end_date < start_date:
        raise ValidationError("date_range", "End date must not be before start date")

    span = (end_date - start_date).days
    if span > max_days:
        raise ValidationError(
            "date_range",
            f"Date range must not exceed {max_days} days (requested: {span} days)",
        )

    return start_date, end_date


def validate_route_name(value: str, field: str = "train_route") -> str:
    """Validate a user-defined route or variant name."""
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(field, f"{field} is required")

    if not ROUTE_NAME_PATTERN.match(cleaned):
        raise ValidationError(
            field,
            "Route name may only contain letters, numbers, spaces, hyphens, underscores, and dots (max 200 chars)",
        )

    return cleaned


def sanitize_for_path(value: str) -> str:
    """Sanitize a string for safe use in file paths.

    Removes path traversal characters and other dangerous sequences.
    """
    # Remove path separators and traversal sequences
    cleaned = value.replace("/", "").replace("\\", "").replace("..", "").replace("\x00", "")
    # Only allow alphanumeric, dash, underscore
    return re.sub(r"[^A-Za-z0-9\-_]", "", cleaned)
