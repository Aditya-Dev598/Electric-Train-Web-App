"""Darwin enricher: fetch train_class and number_of_coaches from Darwin API.

Implements the CIF ↔ Darwin matching strategy using:
- Headcode / trainid (primary — Darwin departure board exposes this)
- Origin departure time (±2 min tolerance)
- Service date

Note: Darwin OpenLDBWS departure board does NOT return the CIF train UID.
It returns `serviceID` (opaque base64 RID) and `trainid` (headcode/RSID).
Matching is therefore headcode + time, NOT uid + time.

Data source: National Rail Darwin OpenLDBWS.
Access: https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/

Performance note: results for a (station, date) pair are cached in-process.
Failures (e.g. 401) are also cached as [] so a bad token doesn't trigger
N HTTP calls per generation run.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone, timedelta
from typing import Optional

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

_DARWIN_LOOKBACK_DAYS = 0
_DARWIN_LOOKAHEAD_DAYS = 7


class DarwinEnricher:
    """Enriches CIF schedules with Darwin formation data (train class, coaches).

    Matching Strategy:
    1. Query Darwin departure board by station CRS + date (cached per date)
    2. Match by headcode + departure time (±2 min) → HIGH
    3. Match by headcode only → MEDIUM
    4. Match by departure time only (±2 min) → LOW
    5. Fetch GetServiceDetails for formation data (coaches, length)
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
        self._departure_cache: dict[tuple[str, date], list[dict]] = {}

        if not self._enabled:
            logger.warning("Darwin API token not configured - enrichment disabled")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def _date_in_darwin_window(self, service_date: date) -> bool:
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

        if not self._date_in_darwin_window(service_date):
            match.failure_reason = "date_outside_darwin_window"
            match.matching_method = "skipped"
            self._log_match(match)
            return match

        try:
            cache_key = (station_crs, service_date)
            if cache_key not in self._departure_cache:
                try:
                    self._departure_cache[cache_key] = self._query_departures(
                        station_crs, service_date
                    )
                except requests.RequestException as exc:
                    logger.warning(
                        "Darwin API error for station %s on %s (caching failure): %s",
                        station_crs, service_date, exc,
                    )
                    self._departure_cache[cache_key] = []
                    raise

            services = self._departure_cache[cache_key]

            if not services:
                match.failure_reason = "no_darwin_services_returned"
                match.matching_method = "no_data"
                self._log_match(match)
                return match

            best_match = self._find_matching_service(schedule, services)

            if best_match:
                match.darwin_rid = best_match.get("rid", "")
                match.matching_method = best_match.get("method", "unknown")
                match.confidence = MatchConfidence(best_match.get("confidence", "FAILED"))

                if match.confidence in (
                    MatchConfidence.EXACT, MatchConfidence.HIGH, MatchConfidence.MEDIUM
                ):
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

    def _query_departures(self, station_crs: str, service_date: date) -> list[dict]:
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
        services = []
        try:
            root = ET.fromstring(xml_text)
            for svc in root.iter():
                tag = svc.tag.split("}")[-1] if "}" in svc.tag else svc.tag
                if tag == "service":
                    service_data = self._extract_service_from_element(svc)
                    if service_data:
                        services.append(service_data)
        except ET.ParseError as exc:
            logger.warning("Failed to parse Darwin XML response: %s", exc)
        return services

    def _extract_service_from_element(self, elem: ET.Element) -> Optional[dict]:
        service: dict = {}
        for child in elem:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            text = (child.text or "").strip()

            if tag == "serviceID":
                service["rid"] = text
            elif tag == "trainid":
                # Darwin trainid is the headcode / RSID (e.g. "2U23", "SW1234")
                service["trainid"] = text
            elif tag == "rsid":
                service["rsid"] = text
            elif tag in ("std", "scheduledDeparture"):
                service["std"] = text
            elif tag in ("etd", "estimatedDeparture"):
                service["etd"] = text
            elif tag == "length":
                try:
                    service["length"] = int(text)
                except (ValueError, TypeError):
                    pass

        return service if "rid" in service else None

    def _find_matching_service(
        self,
        schedule: CIFSchedule,
        darwin_services: list[dict],
    ) -> Optional[dict]:
        """Match a CIF schedule against Darwin departure board services.

        Darwin does not expose the CIF train UID. Matching is:
        1. Headcode + time ±2 min → HIGH
        2. Headcode only → MEDIUM
        3. Time ±2 min only → LOW
        """
        headcode = schedule.train_identity.strip().upper() if schedule.train_identity else ""
        origin = schedule.origin
        cif_minutes = parse_cif_time(origin.departure_time_str) if origin else None

        def _darwin_minutes(svc: dict) -> Optional[int]:
            std = svc.get("std", "")
            if std and ":" in std:
                parts = std.split(":")
                try:
                    return int(parts[0]) * 60 + int(parts[1])
                except (ValueError, IndexError):
                    pass
            return None

        def _headcode_match(svc: dict) -> bool:
            if not headcode:
                return False
            darwin_trainid = (svc.get("trainid") or svc.get("rsid") or "").strip().upper()
            # Darwin trainid may be a 4-char headcode (e.g. "2U23") or a longer RSID
            # (e.g. "SW002300"). Try exact match first, then headcode prefix.
            if darwin_trainid == headcode:
                return True
            if len(headcode) == 4 and darwin_trainid.endswith(headcode):
                return True
            return False

        # Pass 1: headcode + time → HIGH
        for svc in darwin_services:
            dm = _darwin_minutes(svc)
            if _headcode_match(svc) and cif_minutes is not None and dm is not None:
                if abs(cif_minutes - dm) <= 2:
                    return {**svc, "method": "headcode_time", "confidence": "HIGH"}

        # Pass 2: headcode only → MEDIUM
        for svc in darwin_services:
            if _headcode_match(svc):
                return {**svc, "method": "headcode_only", "confidence": "MEDIUM"}

        # Pass 3: time only → LOW
        if cif_minutes is not None:
            for svc in darwin_services:
                dm = _darwin_minutes(svc)
                if dm is not None and abs(cif_minutes - dm) <= 2:
                    return {**svc, "method": "time_only", "confidence": "LOW"}

        return None

    def _get_formation(self, rid: str) -> Optional[dict]:
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
        result: dict = {"train_class": None, "coaches": None}

        try:
            root = ET.fromstring(xml_text)

            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

                # `length` is the most reliable vehicle count field
                if tag in ("length", "trainLength") and result["coaches"] is None:
                    try:
                        result["coaches"] = int((elem.text or "").strip())
                    except (ValueError, TypeError):
                        pass

                elif tag in ("category", "trainClass") and result["train_class"] is None:
                    if elem.text and elem.text.strip():
                        result["train_class"] = elem.text.strip()

                elif tag == "formation":
                    # Darwin XML structure may be:
                    #   <formation><coaches><coach .../></coaches></formation>
                    # or
                    #   <formation><coach .../><coach .../></formation>
                    # Handle both.
                    coach_elements: list[ET.Element] = []
                    for child in elem:
                        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child_tag == "coaches":
                            # Unwrap the <coaches> container
                            coach_elements.extend(list(child))
                        elif child_tag == "coach":
                            coach_elements.append(child)

                    if coach_elements and result["coaches"] is None:
                        coach_count = sum(
                            1 for c in coach_elements
                            if (c.tag.split("}")[-1] if "}" in c.tag else c.tag) == "coach"
                        )
                        if coach_count > 0:
                            result["coaches"] = coach_count

                    if result["train_class"] is None:
                        for coach in coach_elements:
                            class_attr = coach.get("classCode") or coach.get("coachClass", "")
                            if class_attr:
                                result["train_class"] = class_attr
                                break

        except ET.ParseError as exc:
            logger.warning("Failed to parse Darwin formation XML: %s", exc)
            return None

        if result["train_class"] or result["coaches"]:
            return result
        return None

    def _log_match(self, match: DarwinMatch) -> None:
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
