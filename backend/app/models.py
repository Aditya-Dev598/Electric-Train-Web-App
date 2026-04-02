"""Pydantic models for API request/response and internal data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class STPIndicator(str, Enum):
    """Schedule Type / Short Term Plan indicator from CIF BS record."""
    CANCELLATION = "C"
    NEW = "N"
    OVERLAY = "O"
    PERMANENT = "P"


class MatchConfidence(str, Enum):
    """Confidence level for CIF ↔ Darwin matching."""
    EXACT = "EXACT"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# CIF Data Structures
# ---------------------------------------------------------------------------

@dataclass
class CIFLocation:
    """A single location record from a CIF schedule (LO / LI / LT)."""
    record_type: str  # "LO", "LI", "LT"
    tiploc: str
    scheduled_arrival: Optional[str] = None    # HHMM or HHMMS (half-minute)
    scheduled_departure: Optional[str] = None  # HHMM or HHMMS
    public_arrival: Optional[str] = None       # HHMM
    public_departure: Optional[str] = None     # HHMM
    platform: Optional[str] = None
    activity: str = ""  # activity codes, e.g., "T " = stops, "TB" = starts

    @property
    def is_passenger_stop(self) -> bool:
        """Check if this is a passenger calling point."""
        if self.record_type == "LO":
            return True
        if self.record_type == "LT":
            return True
        # For intermediate: check if public times exist or activity includes T (stop)
        if self.public_arrival or self.public_departure:
            return True
        if "T" in self.activity or "D" in self.activity or "U" in self.activity:
            return True
        return False

    @property
    def departure_time_str(self) -> Optional[str]:
        """Best available departure time as HHMM string."""
        return self.public_departure or self.scheduled_departure

    @property
    def arrival_time_str(self) -> Optional[str]:
        """Best available arrival time as HHMM string."""
        return self.public_arrival or self.scheduled_arrival


@dataclass
class CIFSchedule:
    """A parsed CIF schedule (one BS + BX + LO + LI* + LT grouping)."""
    train_uid: str
    date_runs_from: date
    date_runs_to: date
    days_run: str  # 7-char string, e.g. "1111100" = Mon-Fri
    stp_indicator: STPIndicator
    train_status: str = ""
    train_category: str = ""
    train_identity: str = ""  # headcode
    atoc_code: str = ""
    applicable_timetable: str = ""
    locations: list[CIFLocation] = field(default_factory=list)
    bank_holiday_running: str = ""
    # BS record cols 45-47, 48-51, 60 (0-indexed)
    power_type: str = ""    # EMU, DMU, HST, D, E, etc.
    timing_load: str = ""   # unit class code e.g. "321", "387", "444"
    seating_class: str = "" # B=Business+1st+Std, F=1st+Std, S=Std only

    @property
    def origin(self) -> Optional[CIFLocation]:
        for loc in self.locations:
            if loc.record_type == "LO":
                return loc
        return None

    @property
    def terminus(self) -> Optional[CIFLocation]:
        for loc in self.locations:
            if loc.record_type == "LT":
                return loc
        return None

    @property
    def passenger_stops(self) -> list[CIFLocation]:
        return [loc for loc in self.locations if loc.is_passenger_stop]


# ---------------------------------------------------------------------------
# CORPUS Structures
# ---------------------------------------------------------------------------

@dataclass
class StationMapping:
    """Station resolved from CORPUS."""
    station_name: str
    crs_code: str
    tiploc: str
    nlc: str = ""
    stanox: str = ""


# ---------------------------------------------------------------------------
# Darwin Structures
# ---------------------------------------------------------------------------

@dataclass
class DarwinMatch:
    """Result of a CIF ↔ Darwin matching attempt."""
    cif_uid: str
    stp_indicator: str
    service_date: date
    darwin_rid: Optional[str] = None
    matching_method: str = "no_match"
    confidence: MatchConfidence = MatchConfidence.FAILED
    train_class: Optional[str] = None
    number_of_coaches: Optional[int] = None
    failure_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Output Structures
# ---------------------------------------------------------------------------

@dataclass
class TimetableRow:
    """A single row in the timetable CSV output."""
    date: str            # YYYY-MM-DD
    departure_time: str  # HH:MM:SS
    route_variant: str   # auto-generated "Origin - Destination" key into route table
    stop_type: str = "stop"          # "stop" = calls here, "pass" = passes through
    train_class: str = ""            # empty string if unavailable
    number_of_coaches: Optional[int] = None  # None → blank in CSV


@dataclass
class RouteRow:
    """A single row in the route CSV output."""
    route_variant: str
    seq: int
    from_station: str
    to_station: str
    stop_type: str               # "stop" = calling point, "pass" = pass-through
    distance_miles: Optional[float] = None   # None → blank in CSV
    run_min: Optional[int] = None            # None → blank in CSV
    wait_min: Optional[int] = None           # None → blank in CSV
    avg_elevation_m: Optional[float] = None  # average metres above sea level A→B


@dataclass
class DebugRow:
    """A single row in the debug CSV export."""
    service_date: str
    train_uid: str
    stp_indicator: str
    headcode: str
    origin_tiploc: str
    origin_departure: str
    darwin_rid: str
    matching_method: str
    confidence: str
    train_class: str
    number_of_coaches: str
    failure_reason: str
    mileage_status: str
    # CIF BS record fields — present when CIF formation fallback is used
    power_type: str = ""
    timing_load: str = ""
    seating_class: str = ""


@dataclass
class GenerationResult:
    """Complete result of timetable/route generation."""
    timetable_rows: list[TimetableRow] = field(default_factory=list)
    route_rows: list[RouteRow] = field(default_factory=list)
    debug_rows: list[DebugRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
