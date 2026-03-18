"""Unit tests for time_utils module."""

import pytest

from backend.app.utils.time_utils import (
    calculate_run_minutes,
    calculate_wait_minutes,
    minutes_to_hhmmss,
    parse_cif_time,
)


class TestParseCIFTime:
    def test_basic_time(self):
        assert parse_cif_time("0830") == 510  # 8*60+30

    def test_midnight(self):
        assert parse_cif_time("0000") == 0

    def test_end_of_day(self):
        assert parse_cif_time("2359") == 1439

    def test_after_midnight(self):
        # CIF allows times > 2400 for midnight-crossing services
        assert parse_cif_time("2530") == 1530  # 25*60+30

    def test_half_minute(self):
        assert parse_cif_time("0830H") == 511  # Rounds up

    def test_none_input(self):
        assert parse_cif_time(None) is None

    def test_empty_string(self):
        assert parse_cif_time("") is None

    def test_whitespace(self):
        assert parse_cif_time("   ") is None

    def test_invalid_format(self):
        assert parse_cif_time("abc") is None

    def test_too_short(self):
        assert parse_cif_time("08") is None

    def test_padded(self):
        assert parse_cif_time(" 0830 ") == 510


class TestMinutesToHHMMSS:
    def test_basic(self):
        assert minutes_to_hhmmss(510) == "08:30:00"

    def test_midnight(self):
        assert minutes_to_hhmmss(0) == "00:00:00"

    def test_end_of_day(self):
        assert minutes_to_hhmmss(1439) == "23:59:00"

    def test_wraps_past_midnight(self):
        assert minutes_to_hhmmss(1470) == "00:30:00"  # 24:30 wraps to 00:30

    def test_none(self):
        assert minutes_to_hhmmss(None) == ""


class TestCalculateRunMinutes:
    def test_basic(self):
        assert calculate_run_minutes(510, 540) == 30  # 08:30 to 09:00

    def test_midnight_crossing(self):
        # Departs 23:50 (1430), arrives 00:10 (10)
        assert calculate_run_minutes(1430, 10) == 20

    def test_same_time(self):
        assert calculate_run_minutes(510, 510) == 0

    def test_none_dep(self):
        assert calculate_run_minutes(None, 510) is None

    def test_none_arr(self):
        assert calculate_run_minutes(510, None) is None


class TestCalculateWaitMinutes:
    def test_basic_dwell(self):
        assert calculate_wait_minutes(510, 512) == 2

    def test_no_arrival(self):
        assert calculate_wait_minutes(None, 510) == 0

    def test_no_departure(self):
        assert calculate_wait_minutes(510, None) == 0

    def test_zero_dwell(self):
        assert calculate_wait_minutes(510, 510) == 0

    def test_midnight_dwell(self):
        assert calculate_wait_minutes(1438, 2) == 4  # Arrives 23:58, departs 00:02
