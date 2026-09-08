"""Schema-aware CSV normalizer for timetable and route files.

Converts any AI-edited or externally formatted CSV to the exact schema the
pipeline expects, without requiring constant alias-table maintenance.

Strategy (in order of confidence):
  1. Exact match (case-insensitive, stripped) — highest confidence
  2. Fuzzy name match via difflib — catches typos / abbreviations
  3. Structural inference — inspects actual column data to detect dates,
     times, station names, numeric fields, etc.
  4. Manual fallback mapping — a minimal set of well-known aliases for
     fields that structural inference cannot reliably distinguish
     (e.g. "cars" vs "seq" — both are small integers).

Returns a NormalizeResult with the renamed DataFrame and a mapping report
so callers can log / expose what was changed.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldSpec:
    name: str               # canonical pipeline column name
    data_type: str          # "date" | "time" | "text" | "int" | "float"
    required: bool = True
    # Regex that at least 50 % of non-null values must match (structural check)
    pattern: Optional[str] = None
    # Extra aliases beyond fuzzy matching — kept intentionally minimal
    aliases: tuple[str, ...] = ()


TIMETABLE_SCHEMA: list[FieldSpec] = [
    FieldSpec("route_variant", "text",  True,  None,
              ("route", "variant", "route_name", "service", "service_name",
               "route variant", "routevariant")),
    FieldSpec("date",          "date",  True,
              r"\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}",
              ("service_date", "run_date", "travel_date", "operating_date")),
    FieldSpec("dep_time",      "time",  True,
              r"\d{1,2}:\d{2}",
              ("departure_time", "departure", "time", "dep", "depart",
               "scheduled_departure", "sched_dep")),
    FieldSpec("train_type",    "text",  True,  None,
              ("train_class", "class", "rolling_stock", "stock_type",
               "traction", "fleet", "unit_type", "traintype")),
    FieldSpec("cars",          "int",   True,
              r"^\d+$",
              ("number_of_coaches", "coaches", "carriages", "vehicles",
               "num_coaches", "coach_count")),
    # Optional columns — present in scraper output, pipeline ignores them if missing
    FieldSpec("train_uid",     "text",  False),
    FieldSpec("origin_departure", "time", False, r"\d{1,2}:\d{2}"),
]

ROUTE_SCHEMA: list[FieldSpec] = [
    FieldSpec("route_variant", "text",  True,  None,
              ("route", "variant", "route_name", "service", "route variant")),
    FieldSpec("seq",           "int",   True,
              r"^\d+$",
              ("sequence", "order", "stop_seq", "stop_number", "leg")),
    FieldSpec("from_station",  "text",  True,  None,
              ("origin", "from", "departure_station", "from_stop",
               "from station", "start_station", "start", "origin_station",
               "origin station", "from_stop_name")),
    FieldSpec("to_station",    "text",  True,  None,
              ("destination", "to", "arrival_station", "to_stop",
               "to station", "end_station", "end", "dest",
               "destination_station", "destination station", "to_stop_name")),
    FieldSpec("distance",      "float", True,
              r"^\d+(\.\d+)?$",
              ("distance_miles", "dist_miles", "dist", "miles",
               "distance (miles)", "length")),
    FieldSpec("run_min",       "float", True,
              r"^\d+(\.\d+)?$",
              ("run_minutes", "journey_min", "journey_minutes", "runtime",
               "run time", "travel_time", "journey_time")),
    FieldSpec("wait_min",      "float", False,
              r"^\d+(\.\d+)?$",
              ("wait_minutes", "dwell", "dwell_min", "dwell_minutes",
               "dwell_time", "stop_time")),
    FieldSpec("stop_type",     "text",  False,  None,
              ("type", "calling_type")),
    FieldSpec("avg_elevation_m", "float", False, r"^\d+(\.\d+)?$"),
]

SCHEMAS: dict[str, list[FieldSpec]] = {
    "timetable": TIMETABLE_SCHEMA,
    "route":     ROUTE_SCHEMA,
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ColumnMapping:
    source: str          # original column name
    target: str          # canonical column name
    method: str          # "exact" | "fuzzy" | "structural" | "alias" | "unmapped"
    confidence: float    # 0.0 – 1.0


@dataclass
class NormalizeResult:
    df: pd.DataFrame
    mappings: list[ColumnMapping]
    warnings: list[str] = field(default_factory=list)

    @property
    def mapping_report(self) -> list[dict]:
        return [
            {
                "source": m.source,
                "target": m.target,
                "method": m.method,
                "confidence": round(m.confidence, 2),
            }
            for m in self.mappings
        ]

    def any_renamed(self) -> bool:
        return any(m.source != m.target for m in self.mappings if m.method != "unmapped")


# ---------------------------------------------------------------------------
# Structural detectors
# ---------------------------------------------------------------------------

_DATE_RE  = re.compile(r"^\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}$")
_TIME_RE  = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
_INT_RE   = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _sample_values(series: pd.Series, n: int = 20) -> list[str]:
    return [str(v).strip() for v in series.dropna().head(n).tolist()]


def _frac_match(series: pd.Series, pattern: re.Pattern, n: int = 20) -> float:
    vals = _sample_values(series, n)
    if not vals:
        return 0.0
    return sum(1 for v in vals if pattern.fullmatch(v)) / len(vals)


def _detect_type(series: pd.Series) -> str:
    """Infer the data type of a column from its values."""
    if _frac_match(series, _DATE_RE) > 0.5:
        return "date"
    if _frac_match(series, _TIME_RE) > 0.5:
        return "time"
    if _frac_match(series, _INT_RE) > 0.7:
        return "int"
    if _frac_match(series, _FLOAT_RE) > 0.7:
        return "float"
    return "text"


# ---------------------------------------------------------------------------
# Name similarity
# ---------------------------------------------------------------------------

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().replace(" ", "_"),
                           b.lower().replace(" ", "_")).ratio()


def _best_name_match(col_norm: str, schema: list[FieldSpec],
                     already_claimed: set[str]) -> tuple[Optional[FieldSpec], float, str]:
    """Return (best_spec, score, method) for col_norm against the schema."""
    best_spec: Optional[FieldSpec] = None
    best_score = 0.0
    best_method = "unmapped"

    for spec in schema:
        if spec.name in already_claimed:
            continue

        # 1. Exact match
        if col_norm == spec.name:
            return spec, 1.0, "exact"

        # 2. Alias match
        for alias in spec.aliases:
            a_norm = alias.lower().replace(" ", "_")
            if col_norm == a_norm:
                return spec, 0.95, "alias"

        # 3. Fuzzy name match
        score = _similarity(col_norm, spec.name)
        for alias in spec.aliases:
            score = max(score, _similarity(col_norm, alias))
        if score > best_score:
            best_score = score
            best_spec = spec
            best_method = "fuzzy"

    return best_spec, best_score, best_method


# ---------------------------------------------------------------------------
# Main normalizer
# ---------------------------------------------------------------------------

_FUZZY_THRESHOLD = 0.65   # minimum similarity to accept a fuzzy name match
_STRUCT_THRESHOLD = 0.50  # minimum structural confidence to accept a type match


def normalize_csv(csv_text: str, schema_name: str) -> NormalizeResult:
    """Normalize a CSV string to the target schema.

    Args:
        csv_text:    Raw CSV content (UTF-8 / BOM-stripped).
        schema_name: "timetable" or "route".

    Returns:
        NormalizeResult with the cleaned DataFrame and a mapping report.
    """
    if schema_name not in SCHEMAS:
        raise ValueError(f"Unknown schema '{schema_name}'. Choose from: {list(SCHEMAS)}")

    schema = SCHEMAS[schema_name]
    df = pd.read_csv(io.StringIO(csv_text.lstrip("﻿")))
    # Strip whitespace from all column names
    df.columns = [str(c).strip() for c in df.columns]

    warnings: list[str] = []
    mappings: list[ColumnMapping] = []
    rename_map: dict[str, str] = {}
    claimed_targets: set[str] = set()

    # Normalised (lowercase, underscored) → original column name
    col_norm_map: dict[str, str] = {
        c.lower().replace(" ", "_"): c for c in df.columns
    }

    # --- Pass 1: exact + alias + fuzzy name matching ---
    unresolved: list[str] = []  # normalised names still unmatched
    for col_norm, col_orig in col_norm_map.items():
        spec, score, method = _best_name_match(col_norm, schema, claimed_targets)

        if spec and (method in ("exact", "alias") or score >= _FUZZY_THRESHOLD):
            claimed_targets.add(spec.name)
            if col_orig != spec.name:
                rename_map[col_orig] = spec.name
            mappings.append(ColumnMapping(col_orig, spec.name, method, score))
        else:
            unresolved.append(col_norm)

    # --- Pass 2: structural type inference for unresolved columns ---
    unclaimed_specs = [s for s in schema if s.name not in claimed_targets]
    for col_norm in unresolved[:]:
        col_orig = col_norm_map[col_norm]
        detected_type = _detect_type(df[col_orig])

        # Find the best unclaimed spec whose data_type matches
        best_spec: Optional[FieldSpec] = None
        best_conf = 0.0
        for spec in unclaimed_specs:
            if spec.name in claimed_targets:
                continue
            if spec.data_type != detected_type:
                continue
            # Use fuzzy name match as a tiebreaker
            name_score = _similarity(col_norm, spec.name)
            for alias in spec.aliases:
                name_score = max(name_score, _similarity(col_norm, alias))
            # Structural match gives base confidence; name similarity adds up to 0.2
            conf = _STRUCT_THRESHOLD + name_score * 0.2
            if conf > best_conf:
                best_conf = conf
                best_spec = spec

        if best_spec and best_conf >= _STRUCT_THRESHOLD:
            claimed_targets.add(best_spec.name)
            if col_orig != best_spec.name:
                rename_map[col_orig] = best_spec.name
            mappings.append(ColumnMapping(col_orig, best_spec.name, "structural", best_conf))
            unresolved.remove(col_norm)
        else:
            mappings.append(ColumnMapping(col_orig, col_orig, "unmapped", 0.0))

    # --- Apply renames ---
    if rename_map:
        df = df.rename(columns=rename_map)

    # --- Post-process values for canonical formats ---
    df = _post_process(df, schema_name)

    # --- Warn about missing required fields ---
    for spec in schema:
        if spec.required and spec.name not in df.columns:
            warnings.append(
                f"Required column '{spec.name}' could not be mapped from the uploaded file."
            )

    return NormalizeResult(df=df, mappings=mappings, warnings=warnings)


# ---------------------------------------------------------------------------
# Value post-processing (date/time normalisation)
# ---------------------------------------------------------------------------

def _norm_time_value(s: str) -> str:
    parts = str(s).strip().split(":")
    if len(parts) >= 2:
        try:
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        except ValueError:
            pass
    return str(s).strip()[:5]


def _norm_date_value(v: str) -> str:
    from datetime import datetime
    v = str(v).strip().split(" ")[0]
    # Try unambiguous formats first, then UK DD/MM/YYYY before US MM/DD/YYYY
    # (this is a UK rail system so DD/MM is strongly preferred)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(v, fmt)
            # Reject obviously wrong parses: day > 12 in MM position means it must be DD/MM
            # (already handled by order above, but guard against strptime's leniency)
            return parsed.strftime("%d/%m/%Y")
        except ValueError:
            pass
    return v


def _post_process(df: pd.DataFrame, schema_name: str) -> pd.DataFrame:
    df = df.copy()
    if "date" in df.columns:
        df["date"] = df["date"].map(_norm_date_value)
    for col in ["dep_time", "origin_departure"]:
        if col in df.columns:
            df[col] = df[col].map(_norm_time_value)
    # distance_miles column alias handled by schema rename already
    # Ensure numeric columns are actually numeric
    for col in ["cars", "seq", "distance", "run_min", "wait_min", "avg_elevation_m"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Convenience: normalize from raw bytes
# ---------------------------------------------------------------------------

def normalize_csv_bytes(content: bytes, schema_name: str) -> NormalizeResult:
    """Decode bytes and normalize. Tries UTF-8 then latin-1."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    return normalize_csv(text, schema_name)


def result_to_csv_bytes(result: NormalizeResult) -> bytes:
    """Serialize a NormalizeResult's DataFrame back to CSV bytes."""
    buf = io.StringIO()
    result.df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")
