"""Unit tests for CORPUS mapper module."""

import json
import os
import tempfile

import pytest

from backend.app.services.corpus_mapper import CorpusMapper


def _make_corpus_file(entries: list[dict]) -> str:
    """Create a temp CORPUS JSON file with given entries."""
    data = {"TIPLOCDATA": entries}
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return path


SAMPLE_ENTRIES = [
    {"TIPLOC": "KNGX   ", "CRS": "KGX", "NLCDESC": "LONDON KINGS CROSS", "NLC": "1234", "STANOX": "10001"},
    {"TIPLOC": "EUSTON ", "CRS": "EUS", "NLCDESC": "LONDON EUSTON", "NLC": "1235", "STANOX": "10002"},
    {"TIPLOC": "RUGBY  ", "CRS": "RUG", "NLCDESC": "RUGBY", "NLC": "1236", "STANOX": "10003"},
    {"TIPLOC": "BHAMNWS", "CRS": "BHM", "NLCDESC": "BIRMINGHAM NEW STREET", "NLC": "1237", "STANOX": "10004"},
    {"TIPLOC": "BLFR JN", "CRS": "", "NLCDESC": "BLETCHLEY FLYOVER JN", "NLC": "", "STANOX": ""},
]


class TestCorpusMapper:
    def test_load_success(self):
        path = _make_corpus_file(SAMPLE_ENTRIES)
        try:
            mapper = CorpusMapper(path)
            mapper.load()
            assert mapper.is_loaded is True
        finally:
            os.unlink(path)

    def test_load_missing_file(self):
        mapper = CorpusMapper("/nonexistent/corpus.json")
        mapper.load()
        assert mapper.is_loaded is False

    def test_resolve_by_crs(self):
        path = _make_corpus_file(SAMPLE_ENTRIES)
        try:
            mapper = CorpusMapper(path)
            mapper.load()
            result = mapper.resolve_station("KGX")
            assert result is not None
            assert len(result) == 1
            assert result[0].crs_code == "KGX"
            assert result[0].tiploc == "KNGX"
        finally:
            os.unlink(path)

    def test_resolve_by_tiploc(self):
        path = _make_corpus_file(SAMPLE_ENTRIES)
        try:
            mapper = CorpusMapper(path)
            mapper.load()
            result = mapper.resolve_station("EUSTON")
            assert result is not None
            assert result[0].crs_code == "EUS"
        finally:
            os.unlink(path)

    def test_resolve_by_name(self):
        path = _make_corpus_file(SAMPLE_ENTRIES)
        try:
            mapper = CorpusMapper(path)
            mapper.load()
            result = mapper.resolve_station("RUGBY")
            assert result is not None
            # "RUGBY" matches both CRS-length TIPLOC and name - should resolve
            assert any(r.crs_code == "RUG" for r in result)
        finally:
            os.unlink(path)

    def test_resolve_not_found(self):
        path = _make_corpus_file(SAMPLE_ENTRIES)
        try:
            mapper = CorpusMapper(path)
            mapper.load()
            result = mapper.resolve_station("XYZABC")
            assert result is None
        finally:
            os.unlink(path)

    def test_tiploc_to_crs(self):
        path = _make_corpus_file(SAMPLE_ENTRIES)
        try:
            mapper = CorpusMapper(path)
            mapper.load()
            assert mapper.tiploc_to_crs("KNGX") == "KGX"
            assert mapper.tiploc_to_crs("UNKNOWN") is None
        finally:
            os.unlink(path)

    def test_is_passenger_station(self):
        path = _make_corpus_file(SAMPLE_ENTRIES)
        try:
            mapper = CorpusMapper(path)
            mapper.load()
            # Has CRS = passenger station
            assert mapper.is_passenger_station("EUSTON") is True
            # No CRS = junction/non-public
            assert mapper.is_passenger_station("BLFR JN") is False
        finally:
            os.unlink(path)

    def test_provenance(self):
        path = _make_corpus_file(SAMPLE_ENTRIES)
        try:
            mapper = CorpusMapper(path)
            mapper.load()
            prov = mapper.provenance
            assert prov["source"] == "Network Rail CORPUS"
            assert prov["loaded"] is True
            assert prov["tiploc_count"] == 5
            assert prov["crs_count"] == 4  # One has no CRS
        finally:
            os.unlink(path)
