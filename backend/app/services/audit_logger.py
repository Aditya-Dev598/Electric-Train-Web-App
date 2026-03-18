"""Structured audit logging for data provenance and matching decisions."""

from __future__ import annotations

import logging
import json
from datetime import datetime
from typing import Any, Optional


class AuditLogger:
    """Logger that records all data resolution decisions for traceability."""

    def __init__(self, name: str = "rail_timetable.audit") -> None:
        self._logger = logging.getLogger(name)
        self._events: list[dict[str, Any]] = []

    def log_corpus_lookup(
        self,
        query: str,
        result_crs: Optional[str],
        result_tiploc: Optional[str],
        success: bool,
    ) -> None:
        event = {
            "type": "corpus_lookup",
            "timestamp": datetime.utcnow().isoformat(),
            "query": query,
            "result_crs": result_crs,
            "result_tiploc": result_tiploc,
            "success": success,
        }
        self._events.append(event)
        self._logger.info("CORPUS lookup: %s -> CRS=%s TIPLOC=%s (success=%s)",
                          query, result_crs, result_tiploc, success)

    def log_cif_filter(
        self,
        operator: str,
        station_tiploc: str,
        date_range: str,
        total_schedules: int,
        after_stp: int,
        departures_found: int,
    ) -> None:
        event = {
            "type": "cif_filter",
            "timestamp": datetime.utcnow().isoformat(),
            "operator": operator,
            "station_tiploc": station_tiploc,
            "date_range": date_range,
            "total_schedules_scanned": total_schedules,
            "after_stp_overlay": after_stp,
            "departures_at_station": departures_found,
        }
        self._events.append(event)
        self._logger.info(
            "CIF filter: op=%s stn=%s dates=%s scanned=%d after_stp=%d departures=%d",
            operator, station_tiploc, date_range, total_schedules, after_stp, departures_found,
        )

    def log_darwin_match(
        self,
        cif_uid: str,
        stp_indicator: str,
        service_date: str,
        darwin_rid: Optional[str],
        method: str,
        confidence: str,
        train_class: Optional[str],
        coaches: Optional[int],
        failure_reason: Optional[str] = None,
    ) -> None:
        event = {
            "type": "darwin_match",
            "timestamp": datetime.utcnow().isoformat(),
            "cif_uid": cif_uid,
            "stp_indicator": stp_indicator,
            "service_date": service_date,
            "darwin_rid": darwin_rid or "",
            "matching_method": method,
            "confidence": confidence,
            "train_class": train_class or "",
            "number_of_coaches": coaches if coaches is not None else "",
            "failure_reason": failure_reason or "",
        }
        self._events.append(event)
        self._logger.info(
            "Darwin match: UID=%s STP=%s date=%s RID=%s method=%s conf=%s class=%s coaches=%s",
            cif_uid, stp_indicator, service_date, darwin_rid or "NONE",
            method, confidence, train_class or "NULL", str(coaches) if coaches else "NULL",
        )

    def log_mileage_resolution(
        self,
        from_station: str,
        to_station: str,
        distance_miles: Optional[float],
        resolved: bool,
        method: str = "",
    ) -> None:
        event = {
            "type": "mileage_resolution",
            "timestamp": datetime.utcnow().isoformat(),
            "from_station": from_station,
            "to_station": to_station,
            "distance_miles": distance_miles,
            "resolved": resolved,
            "method": method,
        }
        self._events.append(event)
        level = logging.INFO if resolved else logging.WARNING
        self._logger.log(
            level,
            "Mileage: %s -> %s = %s miles (resolved=%s, method=%s)",
            from_station, to_station,
            f"{distance_miles:.2f}" if distance_miles is not None else "NULL",
            resolved, method,
        )

    def log_warning(self, message: str, **context: Any) -> None:
        event = {
            "type": "warning",
            "timestamp": datetime.utcnow().isoformat(),
            "message": message,
            **context,
        }
        self._events.append(event)
        self._logger.warning("WARNING: %s %s", message, json.dumps(context) if context else "")

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()
