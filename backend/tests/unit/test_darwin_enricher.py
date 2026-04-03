"""Unit tests for Darwin enricher with mocked responses."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from backend.app.models import CIFLocation, CIFSchedule, MatchConfidence, STPIndicator
from backend.app.services.audit_logger import AuditLogger
from backend.app.services.darwin_enricher import DarwinEnricher


def _make_schedule(uid: str = "A12345", dep: str = "0830") -> CIFSchedule:
    return CIFSchedule(
        train_uid=uid,
        date_runs_from=date(2026, 3, 1),
        date_runs_to=date(2026, 3, 31),
        days_run="1111111",
        stp_indicator=STPIndicator.PERMANENT,
        atoc_code="VT",
        train_identity="1A23",
        locations=[
            CIFLocation(record_type="LO", tiploc="EUSTON", scheduled_departure=dep, public_departure=dep),
            CIFLocation(record_type="LT", tiploc="BHAMNWS", scheduled_arrival="1015", public_arrival="1015"),
        ],
    )


class TestDarwinEnricherDisabled:
    def test_no_token(self):
        enricher = DarwinEnricher(api_url="http://example.com", api_token="")
        assert enricher.is_enabled is False

    def test_returns_failed_match_when_disabled(self):
        enricher = DarwinEnricher(api_url="http://example.com", api_token="")
        match = enricher.enrich_schedule(_make_schedule(), date(2026, 3, 15), "EUS")
        assert match.confidence == MatchConfidence.FAILED
        assert match.failure_reason == "darwin_api_not_configured"
        assert match.train_class is None
        assert match.number_of_coaches is None


class TestDarwinMatchingLogic:
    def test_headcode_and_time_match(self):
        """Headcode + departure time ±2 min → HIGH confidence."""
        enricher = DarwinEnricher(
            api_url="http://example.com",
            api_token="test-token",
            audit=AuditLogger(),
        )
        services = [
            {"rid": "202603151234", "std": "08:30", "trainid": "1A23"},
        ]
        schedule = _make_schedule(uid="A12345", dep="0830")
        result = enricher._find_matching_service(schedule, services)
        assert result is not None
        assert result["confidence"] == "HIGH"
        assert result["method"] == "headcode_time"

    def test_headcode_time_within_tolerance(self):
        """Headcode + time within ±2 minutes → HIGH confidence."""
        enricher = DarwinEnricher(
            api_url="http://example.com",
            api_token="test-token",
        )
        services = [
            {"rid": "202603151234", "std": "08:31", "trainid": "1A23"},
        ]
        schedule = _make_schedule(uid="A12345", dep="0830")
        result = enricher._find_matching_service(schedule, services)
        assert result is not None
        assert result["confidence"] == "HIGH"

    def test_headcode_only_match(self):
        """Headcode matches but time doesn't → MEDIUM confidence."""
        enricher = DarwinEnricher(
            api_url="http://example.com",
            api_token="test-token",
        )
        services = [
            {"rid": "202603151234", "std": "12:00", "trainid": "1A23"},
        ]
        schedule = _make_schedule(uid="A12345", dep="0830")
        result = enricher._find_matching_service(schedule, services)
        assert result is not None
        assert result["confidence"] == "MEDIUM"
        assert result["method"] == "headcode_only"

    def test_time_only_match(self):
        """Time matches but headcode doesn't → LOW confidence."""
        enricher = DarwinEnricher(
            api_url="http://example.com",
            api_token="test-token",
        )
        services = [
            {"rid": "202603151234", "std": "08:30", "trainid": "9Z99"},
        ]
        schedule = _make_schedule(uid="A12345", dep="0830")
        result = enricher._find_matching_service(schedule, services)
        assert result is not None
        assert result["confidence"] == "LOW"
        assert result["method"] == "time_only"

    def test_no_match(self):
        enricher = DarwinEnricher(
            api_url="http://example.com",
            api_token="test-token",
        )
        services = [
            {"uid": "XXXXXX", "rid": "202603151234", "std": "12:00", "trainid": "9Z99"},
        ]
        schedule = _make_schedule(uid="A12345", dep="0830")
        result = enricher._find_matching_service(schedule, services)
        assert result is None


class TestDarwinFormationParsing:
    def test_parse_formation_with_coaches(self):
        enricher = DarwinEnricher(
            api_url="http://example.com",
            api_token="test-token",
        )
        xml = """<?xml version="1.0"?>
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
        <soap:Body>
            <GetServiceDetailsResponse>
                <trainLength>9</trainLength>
                <category>Express</category>
            </GetServiceDetailsResponse>
        </soap:Body>
        </soap:Envelope>"""

        result = enricher._parse_formation_response(xml)
        assert result is not None
        assert result["coaches"] == 9
        assert result["train_class"] == "Express"

    def test_parse_formation_empty(self):
        enricher = DarwinEnricher(
            api_url="http://example.com",
            api_token="test-token",
        )
        xml = """<?xml version="1.0"?>
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
        <soap:Body>
            <GetServiceDetailsResponse>
            </GetServiceDetailsResponse>
        </soap:Body>
        </soap:Envelope>"""

        result = enricher._parse_formation_response(xml)
        assert result is None  # No formation data
