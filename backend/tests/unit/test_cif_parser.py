"""Unit tests for CIF parser module."""

import os
import tempfile

import pytest

from backend.app.services.cif_parser import CIFParser, _parse_cif_date, _safe_strip
from backend.app.models import STPIndicator


class TestSafeStrip:
    def test_basic(self):
        assert _safe_strip("ABCDEFGH", 2, 5) == "CDE"

    def test_short_string(self):
        assert _safe_strip("AB", 0, 5) == "AB"

    def test_padding(self):
        assert _safe_strip("  ABC  ", 2, 5) == "ABC"

    def test_empty_range(self):
        assert _safe_strip("ABC", 5, 8) == ""


class TestParseCIFDate:
    def test_valid(self):
        d = _parse_cif_date("260315")
        assert d is not None
        assert d.year == 2026
        assert d.month == 3
        assert d.day == 15

    def test_empty(self):
        assert _parse_cif_date("") is None

    def test_invalid(self):
        assert _parse_cif_date("999999") is None

    def test_whitespace(self):
        d = _parse_cif_date(" 260315 ")
        assert d is not None


class TestCIFParser:
    def _write_cif(self, content: str) -> str:
        """Write CIF content to a temp file and return the path."""
        fd, path = tempfile.mkstemp(suffix=".cif")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        return path

    def test_parse_empty_file(self):
        path = self._write_cif("")
        try:
            parser = CIFParser()
            schedules = parser.parse_file(path)
            assert schedules == []
        finally:
            os.unlink(path)

    def test_parse_nonexistent_file(self):
        parser = CIFParser()
        schedules = parser.parse_file("/nonexistent/file.cif")
        assert schedules == []

    def test_parse_basic_schedule(self):
        # Build a minimal CIF with one schedule
        # BS record: cols 0-1=BS, 2=N(new), 3-8=UID, 9-14=from, 15-20=to, 21-27=days, 79=STP
        bs_line = "BSN" + "A12345" + "260301" + "260331" + "1111100" + " " * 51 + "P"
        # BX record: cols 11-12=ATOC
        bx_line = "BX" + " " * 9 + "VT" + "Y"
        # LO record: cols 2-9=TIPLOC, 10-14=dep, 15-18=pub_dep
        lo_line = "LO" + "EUSTON  " + "0830 " + "0830" + " " * 20 + "TB          "
        # LI record
        li_line = "LI" + "RUGBY   " + "0920 " + "0922 " + "     " + "0920" + "0922" + " " * 20 + "T           "
        # LT record: cols 2-9=TIPLOC, 10-14=arr, 15-18=pub_arr
        lt_line = "LT" + "BHAMNWS " + "1015 " + "1015"

        content = "\n".join([bs_line, bx_line, lo_line, li_line, lt_line, "ZZ"])
        path = self._write_cif(content)

        try:
            parser = CIFParser()
            schedules = parser.parse_file(path)

            assert len(schedules) == 1
            sched = schedules[0]
            assert sched.train_uid == "A12345"
            assert sched.atoc_code == "VT"
            assert sched.stp_indicator == STPIndicator.PERMANENT
            assert sched.days_run == "1111100"
            assert len(sched.locations) == 3
            assert sched.locations[0].record_type == "LO"
            assert sched.locations[0].tiploc == "EUSTON"
            assert sched.locations[1].record_type == "LI"
            assert sched.locations[1].tiploc == "RUGBY"
            assert sched.locations[2].record_type == "LT"
            assert sched.locations[2].tiploc == "BHAMNWS"
        finally:
            os.unlink(path)

    def test_provenance(self):
        path = self._write_cif("ZZ")
        try:
            parser = CIFParser()
            parser.parse_file(path)
            prov = parser.provenance
            assert prov["source"] == "Network Rail CIF"
            assert prov["path"] == path
        finally:
            os.unlink(path)
