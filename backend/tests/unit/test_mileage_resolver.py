"""Unit tests for mileage resolver module."""

import json
import os
import tempfile

import pytest

from backend.app.services.mileage_resolver import MileageResolver, chains_to_decimal_miles


class TestChainsToDecimalMiles:
    def test_zero(self):
        assert chains_to_decimal_miles(0, 0) == 0.0

    def test_whole_mile(self):
        assert chains_to_decimal_miles(1, 0) == 1.0

    def test_half_mile(self):
        assert chains_to_decimal_miles(0, 40) == 0.5

    def test_mixed(self):
        assert chains_to_decimal_miles(3, 45) == pytest.approx(3.5625)

    def test_full_chains(self):
        assert chains_to_decimal_miles(0, 80) == 1.0


def _make_mileage_file(segments: list[dict]) -> str:
    data = {"segments": segments}
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


SAMPLE_SEGMENTS = [
    {"from_tiploc": "EUSTON", "to_tiploc": "RUGBY", "elr": "LEC1", "distance_miles": 82.5},
    {"from_tiploc": "RUGBY", "to_tiploc": "BHAMNWS", "elr": "LEC1", "distance_miles": 30.25},
    {"from_tiploc": "EUSTON", "to_tiploc": "RUGBY", "elr": "ALT1", "distance_miles": 85.0},
]


class TestMileageResolver:
    def test_load_success(self):
        path = _make_mileage_file(SAMPLE_SEGMENTS)
        try:
            resolver = MileageResolver(path)
            resolver.load()
            assert resolver.is_loaded is True
        finally:
            os.unlink(path)

    def test_load_missing_file(self):
        resolver = MileageResolver("/nonexistent.json")
        resolver.load()
        assert resolver.is_loaded is False

    def test_get_distance(self):
        path = _make_mileage_file(SAMPLE_SEGMENTS)
        try:
            resolver = MileageResolver(path)
            resolver.load()
            dist = resolver.get_distance("EUSTON", "RUGBY")
            assert dist == 82.5
        finally:
            os.unlink(path)

    def test_get_distance_reverse(self):
        path = _make_mileage_file(SAMPLE_SEGMENTS)
        try:
            resolver = MileageResolver(path)
            resolver.load()
            dist = resolver.get_distance("RUGBY", "EUSTON")
            assert dist == 82.5  # Reverse should work
        finally:
            os.unlink(path)

    def test_get_distance_with_preferred_elr(self):
        path = _make_mileage_file(SAMPLE_SEGMENTS)
        try:
            resolver = MileageResolver(path)
            resolver.load()
            dist = resolver.get_distance("EUSTON", "RUGBY", preferred_elr="ALT1")
            assert dist == 85.0
        finally:
            os.unlink(path)

    def test_get_distance_not_found(self):
        path = _make_mileage_file(SAMPLE_SEGMENTS)
        try:
            resolver = MileageResolver(path)
            resolver.load()
            dist = resolver.get_distance("UNKNOWN", "NOWHERE")
            assert dist is None
        finally:
            os.unlink(path)

    def test_get_distance_with_method(self):
        path = _make_mileage_file(SAMPLE_SEGMENTS)
        try:
            resolver = MileageResolver(path)
            resolver.load()
            dist, method = resolver.get_distance_with_method("EUSTON", "RUGBY")
            assert dist == 82.5
            assert method == "direct_lookup"

            dist2, method2 = resolver.get_distance_with_method("EUSTON", "RUGBY", preferred_elr="ALT1")
            assert dist2 == 85.0
            assert "elr_match" in method2
        finally:
            os.unlink(path)
