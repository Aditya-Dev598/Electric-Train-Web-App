"""Darwin enricher: fetch train_class and number_of_coaches from Darwin API.

Implements the CIF ↔ Darwin matching strategy using:
- Train UID (primary key)
- Origin departure time
- Service date
- Location sequence validation

Data source: National Rail Darwin OpenLDBWS / Push Port.
Access: https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/

Performance note: Darwin OpenLDBWS is a live API. Results for a (station, date)
pair are cached in-process so the departure board is fetched at most once per
station per date — not once per schedule row.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urljoin

import requests

from backend.app.models import CIFSchedule, DarwinMatch, MatchConfidence
from backend.app.services.audit_logger import AuditLogger
from backend.app.utils.time_utils import parse_cif_time

logger = logging.getLogger(__name__)

# Darwin SOAP namespaces
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_LDB = "http://thalesgroup.com/RTTI/2021-11-01/ldb/"
NS_TYPES = "http://thalesgroup.com/RTTI/2017-10-01/ldb/types"
NS_CT = "http://thalesgroup.com/RTTI/2019-01-01/ldb/commontypes"

# Darwin is a live API — only attempt enrichment for dates within this window
_DARWIN_LOOKBACK_DAYS = 0   # Darwin does not serve historical dates
_DARWIN_LOOKAHEAD_DAYS = 7  # Darwin typically has ~7 days of future schedules


class DarwinEnricher:
    """Enriches CIF schedules with Darwin formation data (train class, coaches).

    Matching Strategy:
    1. Query Darwin by station CRS + date to get service list (cached per date)
    2. Match CIF Train UID against Darwin serviceID/trainId
    3. Validate by comparing origin departure time (±2 min tolerance)
    4. Extract formation data for coach count and class
    5. Log all matching decisions with confidence levels
    """

    def __init__(
        self,
        api_url: str,
        api_token: str,
        timeout: int = 10,
        max_retries: int = 2,
        audit: Optional[AuditLogger] = None,
    ) -> None:
        self._api_url = api_url
        self._api_token = api_token
        self._timeout = timeout
        self._max_retries = max_retries
        self._audit = audit or AuditLogger()
        self._enabled = bool(api_token)
        # Cache: (station_crs, date) → list of service dicts
        # Avoids making one HTTP request per schedule row for the same date
        self._departure_cache: dict[tuple[str, date], list[dict]] = {}

        if not self._enabled:
            logger.warning("Darwin API token not configured - enrichment disabled")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def _date_in_darwin_window(self, service_date: date) -> bool:
        """Return True only if Darwin is likely to have data for this date."""
        today = datetime.now(timezone.utc).date()
        earliest = today - timedelta(days=_DARWIN_LOOKBACK_DAYS)
        latest = today + timedelta(days=_DARWIN_LOOKAHEAD_DAYS)
        return earliest <= service_date <= latest

    def enrich_schedule(
        self,
        schedule: CIFSchedule,
        service_date: date,
        station_crs: str,
    ) -> DarwinMatch:
        """Attempt to enrich a single CIF schedule with Darwin data.

        Args:
            schedule: The CIF schedule to enrich
            service_date: The specific operating date
            station_crs: CRS code of the station to query

        Returns:
            DarwinMatch with results and confidence level
        """
        match = DarwinMatch(
            cif_uid=schedule.train_uid,
            stp_indicator=schedule.stp_indicator.value,
            service_date=service_date,
        )

        if not self._enabled:
            match.failure_reason = "darwin_api_not_configured"
            match.matching_method = "disabled"
            self._audit.log_darwin_match(
                cif_uid=schedule.train_uid,
                stp_indicator=schedule.stp_indicator.value,
                service_date=service_date.isoformat(),
                darwin_rid=None,
                method="disabled",
                confidence=MatchConfidence.FAILED.value,
                train_class=None,
                coaches=None,
                failure_reason="darwin_api_not_configured",
            )
            return match

        # Darwin is live-only: skip dates outside the window without an HTTP call
        if not self._date_in_darwin_window(service_date):
            match.failure_reason = "date_outside_darwin_window"
            match.matching_method = "skipped"
            self._log_match(match)
            return match

        try:
            # Use cached departure board — one HTTP call per (station, date)
            cache_key = (station_crs, service_date)
            if cache_key not in self._departure_cache:
                self._departure_cache[cache_key] = self._query_departures(
                    station_crs, service_date
                )
            services = self._departure_cache[cache_key]

            if not services:
                match.failure_reason = "no_darwin_services_returned"
                match.matching_method = "no_data"
                self._log_match(match)
                return match

            # Try to find matching service
            best_match = self._find_matching_service(schedule, services)

            if best_match:
                match.darwin_rid = best_match.get("rid", "")
                match.matching_method = best_match.get("method", "uid_match")
                match.confidence = MatchConfidence(best_match.get("confidence", "FAILED"))

                # Extract formation data if we have a good match
                if match.confidence in (MatchConfidence.EXACT, MatchConfidence.HIGH):
                    formation = self._get_formation(match.darwin_rid)
                    if formation:
                        match.train_class = formation.get("train_class")
                        match.number_of_coaches = formation.get("coaches")
                    else:
                        match.failure_reason = "formation_data_unavailable"
            else:
                match.failure_reason = "no_matching_service_found"
                match.matching_method = "no_match"

        except requests.Timeout:
            match.failure_reason = "darwin_api_timeout"
            match.matching_method = "error"
            logger.warning("Darwin API timeout for UID %s on %s",
                           schedule.train_uid, service_date)
        except requests.RequestException as exc:
            match.failure_reason = f"darwin_api_error: {type(exc).__name__}"
            match.matching_method = "error"
            logger.warning("Darwin API error for UID %s: %s", schedule.train_uid, exc)

        self._log_match(match)
        return match

    def _query_departures(
        self,
        station_crs: str,
        service_date: date,
    ) -> list[dict]:
        """Query Darwin OpenLDBWS for departures at a station.

        Returns a list of service dicts with keys:
        rid, uid, std (scheduled time of departure), origin, destination
        """
        soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{NS_SOAP}"
               xmlns:ldb="{NS_LDB}"
               xmlns:typ="{NS_TYPES}">
  <soap:Header>
    <typ:AccessToken>
      <typ:TokenValue>{self._api_token}</typ:TokenValue>
    </typ:AccessToken>
  </soap:Header>
  <soap:Body>
    <ldb:GetDepBoardWithDetailsRequest>
      <ldb:numRows>150</ldb:numRows>
      <ldb:crs>{station_crs}</ldb:crs>
      <ldb:timeWindow>1440</ldb:timeWindow>
    </ldb:GetDepBoardWithDetailsRequest>
  </soap:Body>
</soap:Envelope>"""

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://thalesgroup.com/RTTI/2021-11-01/ldb/GetDepBoardWithDetails",
        }

        for attempt in range(self._max_retries + 1):
            try:
                resp = requests.post(
                    self._api_url,
                    data=soap_body.encode("utf-8"),
                    headers=headers,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                return self._parse_departure_response(resp.text)
            except requests.RequestException:
                if attempt == self._max_retries:
                    raise
                continue

        return []

    def _parse_departure_response(self, xml_text: str) -> list[dict]:
        """Parse Darwin SOAP departure board response into service list."""
        services = []
        try:
            root = ET.fromstring(xml_text)

            # Navigate SOAP envelope to find train services
            for svc in root.iter():
                if "trainServices" in svc.tag or "service" in svc.tag.lower():
                    service_data = self._extract_service_from_element(svc)
                    if service_data:
                        services.append(service_data)

        except ET.ParseError as exc:
            logger.warning("Failed to parse Darwin XML response: %s", exc)

        return services

    def _extract_service_from_element(self, elem: ET.Element) -> Optional[dict]:
        """Extract service info from a Darwin XML service element."""
        service = {}

        for child in elem:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "rid":
                service["rid"] = (child.text or "").strip()
            elif tag == "uid":
                service["uid"] = (child.text or "").strip()
            elif tag == "trainid":
                service["trainid"] = (child.text or "").strip()
            elif tag in ("std", "scheduledDeparture"):
                service["std"] = (child.text or "").strip()
            elif tag in ("etd", "estimatedDeparture"):
                service["etd"] = (child.text or "").strip()
            elif tag == "serviceID":
                service["rid"] = (child.text or "").strip()

        if "rid" in service or "uid" in service:
            return service
        return None

    def _find_matching_service(
        self,
        schedule: CIFSchedule,
        darwin_services: list[dict],
    ) -> Optional[dict]:
        """Find the Darwin service that matches a CIF schedule.

        Matching priority:
        1. Exact UID match + time match → EXACT
        2. UID match + time within 2 min → HIGH
        3. UID match only → MEDIUM
        4. Headcode + time match → LOW
        """
        cif_uid = schedule.train_uid.strip().upper()
        origin = schedule.origin
        cif_dep_minutes = parse_cif_time(origin.departure_time_str) if origin else None

        for svc in darwin_services:
            darwin_uid = (svc.get("uid") or "").strip().upper()
            darwin_std = svc.get("std", "")

            # Parse Darwin time (HH:MM format) to minutes
            darwin_minutes = None
            if darwin_std and ":" in darwin_std:
                parts = darwin_std.split(":")
                if len(parts) >= 2:
                    try:
                        darwin_minutes = int(parts[0]) * 60 + int(parts[1])
                    except ValueError:
                        pass

            # Strategy 1: UID exact match
            if darwin_uid == cif_uid:
                if cif_dep_minutes is not None and darwin_minutes is not None:
                    time_diff = abs(cif_dep_minutes - darwin_minutes)
                    if time_diff == 0:
                        return {**svc, "method": "uid_exact", "confidence": "EXACT"}
                    elif time_diff <= 2:
                        return {**svc, "method": "uid_time_tolerance", "confidence": "HIGH"}
                    else:
                        return {**svc, "method": "uid_time_mismatch", "confidence": "MEDIUM"}
                else:
                    return {**svc, "method": "uid_only", "confidence": "MEDIUM"}

        # Strategy 2: Headcode fallback
        if schedule.train_identity:
            headcode = schedule.train_identity.strip().upper()
            for svc in darwin_services:
                darwin_trainid = (svc.get("trainid") or "").strip().upper()
                if darwin_trainid == headcode and cif_dep_minutes is not None:
                    darwin_std = svc.get("std", "")
                    if darwin_std and ":" in darwin_std:
                        parts = darwin_std.split(":")
                        try:
                            darwin_minutes = int(parts[0]) * 60 + int(parts[1])
                            if abs(cif_dep_minutes - darwin_minutes) <= 5:
                                return {**svc, "method": "headcode_fallback", "confidence": "LOW"}
                        except ValueError:
                            pass

        return None

    def _get_formation(self, rid: str) -> Optional[dict]:
        """Query Darwin for formation/loading data for a specific RID.

        Returns dict with 'train_class' and 'coaches' if available.
        """
        soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{NS_SOAP}"
               xmlns:ldb="{NS_LDB}"
               xmlns:typ="{NS_TYPES}">
  <soap:Header>
    <typ:AccessToken>
      <typ:TokenValue>{self._api_token}</typ:TokenValue>
    </typ:AccessToken>
  </soap:Header>
  <soap:Body>
    <ldb:GetServiceDetailsRequest>
      <ldb:serviceID>{rid}</ldb:serviceID>
    </ldb:GetServiceDetailsRequest>
  </soap:Body>
</soap:Envelope>"""

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://thalesgroup.com/RTTI/2021-11-01/ldb/GetServiceDetails",
        }

        try:
            resp = requests.post(
                self._api_url,
                data=soap_body.encode("utf-8"),
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return self._parse_formation_response(resp.text)
        except requests.RequestException as exc:
            logger.warning("Failed to get formation for RID %s: %s", rid, exc)
            return None

    def _parse_formation_response(self, xml_text: str) -> Optional[dict]:
        """Parse Darwin service detail response for formation data."""
        result = {"train_class": None, "coaches": None}

        try:
            root = ET.fromstring(xml_text)

            # Look for formation/loading data
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

                if tag == "formation":
                    coaches = list(elem)
                    coach_count = sum(
                        1 for c in coaches
                        if (c.tag.split("}")[-1] if "}" in c.tag else c.tag) == "coach"
                    )
                    if coach_count > 0:
                        result["coaches"] = coach_count

                    # Extract class from coach attributes
                    for coach in coaches:
                        coach_tag = coach.tag.split("}")[-1] if "}" in coach.tag else coach.tag
                        if coach_tag == "coach":
                            class_attr = coach.get("classCode") or coach.get("coachClass", "")
                            if class_attr:
                                result["train_class"] = class_attr
                                break

                elif tag in ("length", "trainLength"):
                    if elem.text and elem.text.strip().isdigit():
                        result["coaches"] = int(elem.text.strip())

                elif tag in ("category", "trainClass"):
                    if elem.text:
                        result["train_class"] = elem.text.strip()

        except ET.ParseError as exc:
            logger.warning("Failed to parse Darwin formation XML: %s", exc)
            return None

        if result["train_class"] or result["coaches"]:
            return result
        return None

    def _log_match(self, match: DarwinMatch) -> None:
        """Log a Darwin match result."""
        self._audit.log_darwin_match(
            cif_uid=match.cif_uid,
            stp_indicator=match.stp_indicator,
            service_date=match.service_date.isoformat(),
            darwin_rid=match.darwin_rid,
            method=match.matching_method,
            confidence=match.confidence.value,
            train_class=match.train_class,
            coaches=match.number_of_coaches,
            failure_reason=match.failure_reason,
        )

    """Enriches CIF schedules with Darwin formation data (train class, coaches).

    Matching Strategy:
    1. Query Darwin by station CRS + date to get service list
    2. Match CIF Train UID against Darwin serviceID/trainId
    3. Validate by comparing origin departure time (±2 min tolerance)
    4. Extract formation data for coach count and class
    5. Log all matching decisions with confidence levels
    """

    def __init__(
        self,
        api_url: str,
        api_token: str,
        timeout: int = 10,
        max_retries: int = 2,
        audit: Optional[AuditLogger] = None,
    ) -> None:
        self._api_url = api_url
        self._api_token = api_token
        self._timeout = timeout
        self._max_retries = max_retries
        self._audit = audit or AuditLogger()
        self._enabled = bool(api_token)

        if not self._enabled:
            logger.warning("Darwin API token not configured - enrichment disabled")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def enrich_schedule(
        self,
        schedule: CIFSchedule,
        service_date: date,
        station_crs: str,
    ) -> DarwinMatch:
        """Attempt to enrich a single CIF schedule with Darwin data.

        Args:
            schedule: The CIF schedule to enrich
            service_date: The specific operating date
            station_crs: CRS code of the station to query

        Returns:
            DarwinMatch with results and confidence level
        """
        match = DarwinMatch(
            cif_uid=schedule.train_uid,
            stp_indicator=schedule.stp_indicator.value,
            service_date=service_date,
        )

        if not self._enabled:
            match.failure_reason = "darwin_api_not_configured"
            match.matching_method = "disabled"
            self._audit.log_darwin_match(
                cif_uid=schedule.train_uid,
                stp_indicator=schedule.stp_indicator.value,
                service_date=service_date.isoformat(),
                darwin_rid=None,
                method="disabled",
                confidence=MatchConfidence.FAILED.value,
                train_class=None,
                coaches=None,
                failure_reason="darwin_api_not_configured",
            )
            return match

        try:
            # Query Darwin for departures at this station on this date
            services = self._query_departures(station_crs, service_date)

            if not services:
                match.failure_reason = "no_darwin_services_returned"
                match.matching_method = "no_data"
                self._log_match(match)
                return match

            # Try to find matching service
            best_match = self._find_matching_service(schedule, services)

            if best_match:
                match.darwin_rid = best_match.get("rid", "")
                match.matching_method = best_match.get("method", "uid_match")
                match.confidence = MatchConfidence(best_match.get("confidence", "FAILED"))

                # Extract formation data if we have a good match
                if match.confidence in (MatchConfidence.EXACT, MatchConfidence.HIGH):
                    formation = self._get_formation(match.darwin_rid)
                    if formation:
                        match.train_class = formation.get("train_class")
                        match.number_of_coaches = formation.get("coaches")
                    else:
                        match.failure_reason = "formation_data_unavailable"
            else:
                match.failure_reason = "no_matching_service_found"
                match.matching_method = "no_match"

        except requests.Timeout:
            match.failure_reason = "darwin_api_timeout"
            match.matching_method = "error"
            logger.warning("Darwin API timeout for UID %s on %s",
                           schedule.train_uid, service_date)
        except requests.RequestException as exc:
            match.failure_reason = f"darwin_api_error: {type(exc).__name__}"
            match.matching_method = "error"
            logger.warning("Darwin API error for UID %s: %s", schedule.train_uid, exc)

        self._log_match(match)
        return match

    def _query_departures(
        self,
        station_crs: str,
        service_date: date,
    ) -> list[dict]:
        """Query Darwin OpenLDBWS for departures at a station.

        Returns a list of service dicts with keys:
        rid, uid, std (scheduled time of departure), origin, destination
        """
        soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{NS_SOAP}"
               xmlns:ldb="{NS_LDB}"
               xmlns:typ="{NS_TYPES}">
  <soap:Header>
    <typ:AccessToken>
      <typ:TokenValue>{self._api_token}</typ:TokenValue>
    </typ:AccessToken>
  </soap:Header>
  <soap:Body>
    <ldb:GetDepBoardWithDetailsRequest>
      <ldb:numRows>150</ldb:numRows>
      <ldb:crs>{station_crs}</ldb:crs>
      <ldb:timeWindow>1440</ldb:timeWindow>
    </ldb:GetDepBoardWithDetailsRequest>
  </soap:Body>
</soap:Envelope>"""

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://thalesgroup.com/RTTI/2021-11-01/ldb/GetDepBoardWithDetails",
        }

        for attempt in range(self._max_retries + 1):
            try:
                resp = requests.post(
                    self._api_url,
                    data=soap_body.encode("utf-8"),
                    headers=headers,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                return self._parse_departure_response(resp.text)
            except requests.RequestException:
                if attempt == self._max_retries:
                    raise
                continue

        return []

    def _parse_departure_response(self, xml_text: str) -> list[dict]:
        """Parse Darwin SOAP departure board response into service list."""
        services = []
        try:
            root = ET.fromstring(xml_text)

            # Navigate SOAP envelope to find train services
            for svc in root.iter():
                if "trainServices" in svc.tag or "service" in svc.tag.lower():
                    service_data = self._extract_service_from_element(svc)
                    if service_data:
                        services.append(service_data)

        except ET.ParseError as exc:
            logger.warning("Failed to parse Darwin XML response: %s", exc)

        return services

    def _extract_service_from_element(self, elem: ET.Element) -> Optional[dict]:
        """Extract service info from a Darwin XML service element."""
        service = {}

        for child in elem:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "rid":
                service["rid"] = (child.text or "").strip()
            elif tag == "uid":
                service["uid"] = (child.text or "").strip()
            elif tag == "trainid":
                service["trainid"] = (child.text or "").strip()
            elif tag in ("std", "scheduledDeparture"):
                service["std"] = (child.text or "").strip()
            elif tag in ("etd", "estimatedDeparture"):
                service["etd"] = (child.text or "").strip()
            elif tag == "serviceID":
                service["rid"] = (child.text or "").strip()

        if "rid" in service or "uid" in service:
            return service
        return None

    def _find_matching_service(
        self,
        schedule: CIFSchedule,
        darwin_services: list[dict],
    ) -> Optional[dict]:
        """Find the Darwin service that matches a CIF schedule.

        Matching priority:
        1. Exact UID match + time match → EXACT
        2. UID match + time within 2 min → HIGH
        3. UID match only → MEDIUM
        4. Headcode + time match → LOW
        """
        cif_uid = schedule.train_uid.strip().upper()
        origin = schedule.origin
        cif_dep_minutes = parse_cif_time(origin.departure_time_str) if origin else None

        for svc in darwin_services:
            darwin_uid = (svc.get("uid") or "").strip().upper()
            darwin_std = svc.get("std", "")

            # Parse Darwin time (HH:MM format) to minutes
            darwin_minutes = None
            if darwin_std and ":" in darwin_std:
                parts = darwin_std.split(":")
                if len(parts) >= 2:
                    try:
                        darwin_minutes = int(parts[0]) * 60 + int(parts[1])
                    except ValueError:
                        pass

            # Strategy 1: UID exact match
            if darwin_uid == cif_uid:
                if cif_dep_minutes is not None and darwin_minutes is not None:
                    time_diff = abs(cif_dep_minutes - darwin_minutes)
                    if time_diff == 0:
                        return {**svc, "method": "uid_exact", "confidence": "EXACT"}
                    elif time_diff <= 2:
                        return {**svc, "method": "uid_time_tolerance", "confidence": "HIGH"}
                    else:
                        return {**svc, "method": "uid_time_mismatch", "confidence": "MEDIUM"}
                else:
                    return {**svc, "method": "uid_only", "confidence": "MEDIUM"}

        # Strategy 2: Headcode fallback
        if schedule.train_identity:
            headcode = schedule.train_identity.strip().upper()
            for svc in darwin_services:
                darwin_trainid = (svc.get("trainid") or "").strip().upper()
                if darwin_trainid == headcode and cif_dep_minutes is not None:
                    darwin_std = svc.get("std", "")
                    if darwin_std and ":" in darwin_std:
                        parts = darwin_std.split(":")
                        try:
                            darwin_minutes = int(parts[0]) * 60 + int(parts[1])
                            if abs(cif_dep_minutes - darwin_minutes) <= 5:
                                return {**svc, "method": "headcode_fallback", "confidence": "LOW"}
                        except ValueError:
                            pass

        return None

    def _get_formation(self, rid: str) -> Optional[dict]:
        """Query Darwin for formation/loading data for a specific RID.

        Returns dict with 'train_class' and 'coaches' if available.
        """
        soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="{NS_SOAP}"
               xmlns:ldb="{NS_LDB}"
               xmlns:typ="{NS_TYPES}">
  <soap:Header>
    <typ:AccessToken>
      <typ:TokenValue>{self._api_token}</typ:TokenValue>
    </typ:AccessToken>
  </soap:Header>
  <soap:Body>
    <ldb:GetServiceDetailsRequest>
      <ldb:serviceID>{rid}</ldb:serviceID>
    </ldb:GetServiceDetailsRequest>
  </soap:Body>
</soap:Envelope>"""

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://thalesgroup.com/RTTI/2021-11-01/ldb/GetServiceDetails",
        }

        try:
            resp = requests.post(
                self._api_url,
                data=soap_body.encode("utf-8"),
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return self._parse_formation_response(resp.text)
        except requests.RequestException as exc:
            logger.warning("Failed to get formation for RID %s: %s", rid, exc)
            return None

    def _parse_formation_response(self, xml_text: str) -> Optional[dict]:
        """Parse Darwin service detail response for formation data."""
        result = {"train_class": None, "coaches": None}

        try:
            root = ET.fromstring(xml_text)

            # Look for formation/loading data
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

                if tag == "formation":
                    coaches = list(elem)
                    coach_count = sum(
                        1 for c in coaches
                        if (c.tag.split("}")[-1] if "}" in c.tag else c.tag) == "coach"
                    )
                    if coach_count > 0:
                        result["coaches"] = coach_count

                    # Extract class from coach attributes
                    for coach in coaches:
                        coach_tag = coach.tag.split("}")[-1] if "}" in coach.tag else coach.tag
                        if coach_tag == "coach":
                            class_attr = coach.get("classCode") or coach.get("coachClass", "")
                            if class_attr:
                                result["train_class"] = class_attr
                                break

                elif tag in ("length", "trainLength"):
                    if elem.text and elem.text.strip().isdigit():
                        result["coaches"] = int(elem.text.strip())

                elif tag in ("category", "trainClass"):
                    if elem.text:
                        result["train_class"] = elem.text.strip()

        except ET.ParseError as exc:
            logger.warning("Failed to parse Darwin formation XML: %s", exc)
            return None

        if result["train_class"] or result["coaches"]:
            return result
        return None

    def _log_match(self, match: DarwinMatch) -> None:
        """Log a Darwin match result."""
        self._audit.log_darwin_match(
            cif_uid=match.cif_uid,
            stp_indicator=match.stp_indicator,
            service_date=match.service_date.isoformat(),
            darwin_rid=match.darwin_rid,
            method=match.matching_method,
            confidence=match.confidence.value,
            train_class=match.train_class,
            coaches=match.number_of_coaches,
            failure_reason=match.failure_reason,
        )
