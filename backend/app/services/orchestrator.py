"""Orchestrator service: coordinates all modules to generate timetable and route CSVs.

This is the main business logic entry point that:
1. Validates inputs
2. Resolves station via CORPUS
3. Parses and filters CIF schedules
4. Applies STP overlays per date
5. Enriches via Darwin
6. Builds route patterns with auto-generated variant names
7. Generates all CSV outputs
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from backend.app.models import (
    DebugRow,
    GenerationResult,
    MatchConfidence,
    RouteRow,
    TimetableRow,
)
from backend.app.services.audit_logger import AuditLogger
from backend.app.services.cif_parser import CIFParser
from backend.app.services.corpus_mapper import CorpusMapper
from backend.app.services.csv_exporter import (
    generate_debug_csv,
    generate_route_csv,
    generate_timetable_csv,
)
from backend.app.services.cif_formation import cif_coach_count, cif_train_class
from backend.app.services.darwin_enricher import DarwinEnricher
from backend.app.services.mileage_resolver import MileageResolver
from backend.app.services.route_builder import (
    build_route_rows,
    extract_stopping_pattern,
    generate_variant_names,
    identify_unique_routes,
)
from backend.app.services.schedule_validator import (
    apply_stp_overlays,
    expand_date_range,
    filter_schedules,
    get_departure_at_station,
    get_stop_type_at_station,
)
from backend.app.utils.time_utils import minutes_to_hhmmss, parse_cif_time

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates the full timetable/route generation pipeline."""

    def __init__(
        self,
        corpus: CorpusMapper,
        cif_parser: CIFParser,
        mileage: MileageResolver,
        darwin: DarwinEnricher,
        audit: AuditLogger,
    ) -> None:
        self._corpus = corpus
        self._cif = cif_parser
        self._mileage = mileage
        self._darwin = darwin
        self._audit = audit

    def generate(
        self,
        station_name: str,
        operator_code: str,
        date_start: date,
        date_end: date,
    ) -> GenerationResult:
        """Run the full generation pipeline.

        Returns a GenerationResult with timetable rows, route rows,
        debug rows, warnings, provenance, and summary statistics.
        """
        result = GenerationResult()
        result.provenance = {
            "cif": self._cif.provenance,
            "corpus": self._corpus.provenance,
            "mileage": self._mileage.provenance,
        }

        # --- Step 1: CORPUS lookup ---
        station_mappings = self._corpus.resolve_station(station_name)
        if not station_mappings:
            result.warnings.append(
                f"Station '{station_name}' not found in CORPUS (exact match only). "
                "Try using CRS code (e.g. KGX) or TIPLOC."
            )
            self._audit.log_corpus_lookup(station_name, None, None, False)
            return result

        # Use first match as the primary (for display / audit), but collect ALL
        # TIPLOCs from every returned mapping so we don't miss alternate CIF entries
        # for the same physical station (e.g. Waterloo has WATRLOO + WATRLMN in CIF).
        station = station_mappings[0]
        all_tiplocs = list(dict.fromkeys(
            m.tiploc.upper() for m in station_mappings if m.tiploc
        ))
        if len(station_mappings) > 1:
            extra_crs = [m.crs_code for m in station_mappings[1:] if m.crs_code]
            if extra_crs:
                result.warnings.append(
                    f"Multiple CRS/TIPLOCs matched '{station_name}' — searching across all: "
                    f"{', '.join(all_tiplocs)}."
                )

        self._audit.log_corpus_lookup(
            station_name, station.crs_code, station.tiploc, True,
        )

        tiploc = station.tiploc.upper()  # primary (used for display)
        crs = station.crs_code.upper() if station.crs_code else ""

        # --- Step 2: Filter CIF schedules ---
        all_schedules = self._cif.schedules
        filtered = filter_schedules(all_schedules, operator_code, station_tiplocs=all_tiplocs)

        if not filtered:
            if not all_schedules:
                result.warnings.append(
                    "No CIF data is loaded. Ensure CIF/MCA timetable files are present "
                    f"in the configured CIF_DATA_PATH directory (currently: "
                    f"'{self._cif.provenance.get('path', 'data/cif')}'). "
                    "Download from Network Rail Datafeeds or Rail Data Marketplace."
                )
            else:
                by_operator = filter_schedules(all_schedules, operator_code, None)
                if not by_operator:
                    known_ops = sorted({s.atoc_code for s in all_schedules if s.atoc_code})
                    result.warnings.append(
                        f"No CIF schedules found for operator '{operator_code}'. "
                        f"Operators present in loaded CIF data: {', '.join(known_ops) or 'none'}. "
                        "Check the operator ATOC code (e.g. SN=Southern, GX=Gatwick Express)."
                    )
                else:
                    result.warnings.append(
                        f"Operator '{operator_code}' has {len(by_operator)} schedule(s) in CIF "
                        f"but none call at station '{tiploc}' ({crs}). "
                        "Verify the station TIPLOC or CRS code is correct."
                    )
            return result

        # --- Step 3: Expand dates and apply STP overlays ---
        dates = expand_date_range(date_start, date_end)
        total_services = 0

        all_effective_schedules: list[tuple[date, list]] = []

        for d in dates:
            effective = apply_stp_overlays(filtered, d)
            all_effective_schedules.append((d, effective))
            total_services += len(effective)

        self._audit.log_cif_filter(
            operator=operator_code,
            station_tiploc=tiploc,
            date_range=f"{date_start.isoformat()} to {date_end.isoformat()}",
            total_schedules=len(all_schedules),
            after_stp=total_services,
            departures_found=0,  # Updated below
        )

        # --- Step 4: Collect unique schedules that have a time at the requested station ---
        # Deduplicate by (train_uid, stp_indicator, date_runs_from) so the same schedule
        # isn't processed multiple times across dates (once per date it runs).
        all_effective_flat: list = []
        _seen_schedules: set = set()
        for _, effective in all_effective_schedules:
            for schedule in effective:
                key = (schedule.train_uid, schedule.stp_indicator, schedule.date_runs_from)
                if key not in _seen_schedules and get_departure_at_station(schedule, all_tiplocs):
                    _seen_schedules.add(key)
                    all_effective_flat.append(schedule)

        # --- Step 5: Build route patterns and assign variant names ---
        # Must happen before timetable rows so each row can reference its variant.
        unique_routes = identify_unique_routes(all_effective_flat, self._corpus, all_tiplocs[0])
        pattern_to_variant = generate_variant_names(unique_routes, self._corpus)

        for pattern, representative_schedule in unique_routes.items():
            variant_name = pattern_to_variant[pattern]
            route_rows = build_route_rows(
                representative_schedule,
                variant_name,
                self._corpus,
                self._mileage,
                self._audit,
                focus_tiploc=tiploc,
            )
            result.route_rows.extend(route_rows)

        # --- Step 6: Generate timetable rows ---
        departures_found = 0
        darwin_success = 0
        darwin_attempts = 0

        for d, effective in all_effective_schedules:
            for schedule in effective:
                dep_time_raw = get_departure_at_station(schedule, all_tiplocs)
                if not dep_time_raw:
                    continue

                departures_found += 1
                dep_minutes = parse_cif_time(dep_time_raw)
                dep_formatted = minutes_to_hhmmss(dep_minutes)

                # Look up route_variant for this schedule's stopping pattern
                pattern = extract_stopping_pattern(schedule, self._corpus, all_tiplocs[0])
                variant_name = pattern_to_variant.get(pattern, "")

                # Darwin enrichment
                darwin_attempts += 1
                darwin_match = self._darwin.enrich_schedule(schedule, d, crs)

                train_class = ""
                coaches: Optional[int] = None

                if darwin_match.confidence in (MatchConfidence.EXACT, MatchConfidence.HIGH, MatchConfidence.MEDIUM):
                    darwin_success += 1
                    if darwin_match.train_class:
                        train_class = darwin_match.train_class
                    if darwin_match.number_of_coaches is not None:
                        coaches = darwin_match.number_of_coaches

                # CIF fallback: if Darwin didn't supply class/coaches, derive from BS record
                if not train_class:
                    train_class = cif_train_class(schedule.seating_class)
                if coaches is None:
                    coaches = cif_coach_count(schedule.timing_load, schedule.power_type)

                stop_type = get_stop_type_at_station(schedule, all_tiplocs) or "stop"

                result.timetable_rows.append(TimetableRow(
                    date=d.isoformat(),
                    departure_time=dep_formatted,
                    route_variant=variant_name,
                    stop_type=stop_type,
                    train_class=train_class,
                    number_of_coaches=coaches,
                ))

                # Debug row
                origin = schedule.origin
                result.debug_rows.append(DebugRow(
                    service_date=d.isoformat(),
                    train_uid=schedule.train_uid,
                    stp_indicator=schedule.stp_indicator.value,
                    headcode=schedule.train_identity,
                    origin_tiploc=origin.tiploc if origin else "",
                    origin_departure=dep_time_raw,
                    darwin_rid=darwin_match.darwin_rid or "",
                    matching_method=darwin_match.matching_method,
                    confidence=darwin_match.confidence.value,
                    train_class=train_class,
                    number_of_coaches=str(coaches) if coaches is not None else "",
                    failure_reason=darwin_match.failure_reason or "",
                    mileage_status="",  # Populated during route build
                    power_type=schedule.power_type,
                    timing_load=schedule.timing_load,
                    seating_class=schedule.seating_class,
                ))

        if not departures_found:
            result.warnings.append(
                f"No departures found at station '{tiploc}' ({crs}) for the "
                f"selected dates and operator. The station may only be an arrival point."
            )

        # Sort timetable rows chronologically: date first, then departure time within each date
        result.timetable_rows.sort(key=lambda r: (r.date, r.departure_time))

        # --- Step 7: Warnings for missing data ---
        missing_mileage = sum(1 for r in result.route_rows if not r.distance_miles)
        if missing_mileage:
            result.warnings.append(
                f"{missing_mileage} route segment(s) have no mileage data. "
                "Official NESA mileage data may not cover these segments."
            )

        if darwin_attempts > 0 and darwin_success == 0 and self._darwin.is_enabled:
            result.warnings.append(
                "Darwin enrichment failed for all services. "
                "train_class and number_of_coaches will be empty."
            )
        elif not self._darwin.is_enabled:
            result.warnings.append(
                "Darwin API token not configured. "
                "train_class and number_of_coaches fields will be empty."
            )

        # --- Summary ---
        result.summary = {
            "total_services_processed": total_services,
            "timetable_rows_generated": len(result.timetable_rows),
            "unique_route_patterns": len(unique_routes),
            "darwin_enrichment_success_pct": (
                round(darwin_success / darwin_attempts * 100, 1)
                if darwin_attempts > 0
                else 0.0
            ),
            "darwin_attempts": darwin_attempts,
            "darwin_successes": darwin_success,
            "missing_mileage_count": missing_mileage,
            "dates_in_range": len(dates),
            "departures_found": departures_found,
        }

        logger.info(
            "Generation complete: %d timetable rows, %d route rows, %d warnings",
            len(result.timetable_rows),
            len(result.route_rows),
            len(result.warnings),
        )

        return result
