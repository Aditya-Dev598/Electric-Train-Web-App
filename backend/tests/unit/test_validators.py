"""Unit tests for security validators."""

from datetime import date

import pytest

from backend.app.security.validators import (
    ValidationError,
    sanitize_for_path,
    validate_date,
    validate_date_range,
    validate_operator_code,
    validate_route_name,
    validate_station_name,
)


class TestValidateStationName:
    def test_crs_code(self):
        assert validate_station_name("KGX") == "KGX"

    def test_tiploc(self):
        assert validate_station_name("EUSTON") == "EUSTON"

    def test_station_name(self):
        assert validate_station_name("London Kings Cross") == "London Kings Cross"

    def test_empty(self):
        with pytest.raises(ValidationError):
            validate_station_name("")

    def test_special_chars(self):
        with pytest.raises(ValidationError):
            validate_station_name("station; DROP TABLE")

    def test_path_traversal(self):
        with pytest.raises(ValidationError):
            validate_station_name("../../etc/passwd")


class TestValidateOperatorCode:
    def test_valid_two_letter(self):
        assert validate_operator_code("VT") == "VT"

    def test_valid_three_letter(self):
        assert validate_operator_code("SWR") == "SWR"

    def test_lowercase_normalized(self):
        assert validate_operator_code("vt") == "VT"

    def test_empty(self):
        with pytest.raises(ValidationError):
            validate_operator_code("")

    def test_too_long(self):
        with pytest.raises(ValidationError):
            validate_operator_code("ABCD")

    def test_numbers(self):
        with pytest.raises(ValidationError):
            validate_operator_code("12")

    def test_allowlist(self):
        assert validate_operator_code("VT", allowed=["VT", "GW"]) == "VT"

    def test_allowlist_rejected(self):
        with pytest.raises(ValidationError):
            validate_operator_code("XC", allowed=["VT", "GW"])


class TestValidateDate:
    def test_valid(self):
        d = validate_date("2026-03-15")
        assert d == date(2026, 3, 15)

    def test_empty(self):
        with pytest.raises(ValidationError):
            validate_date("")

    def test_wrong_format(self):
        with pytest.raises(ValidationError):
            validate_date("15/03/2026")

    def test_invalid_date(self):
        with pytest.raises(ValidationError):
            validate_date("2026-02-30")


class TestValidateDateRange:
    def test_valid_range(self):
        start, end = validate_date_range("2026-03-01", "2026-03-15")
        assert start == date(2026, 3, 1)
        assert end == date(2026, 3, 15)

    def test_end_before_start(self):
        with pytest.raises(ValidationError):
            validate_date_range("2026-03-15", "2026-03-01")

    def test_exceeds_max(self):
        with pytest.raises(ValidationError):
            validate_date_range("2026-01-01", "2026-12-31", max_days=31)

    def test_same_day(self):
        start, end = validate_date_range("2026-03-15", "2026-03-15")
        assert start == end


class TestValidateRouteName:
    def test_valid(self):
        assert validate_route_name("London-Edinburgh Main") == "London-Edinburgh Main"

    def test_empty(self):
        with pytest.raises(ValidationError):
            validate_route_name("")

    def test_injection(self):
        with pytest.raises(ValidationError):
            validate_route_name("=cmd()|'/bin/sh'!A0")


class TestSanitizeForPath:
    def test_normal(self):
        assert sanitize_for_path("myfile") == "myfile"

    def test_traversal(self):
        assert ".." not in sanitize_for_path("../../etc/passwd")

    def test_slash_removal(self):
        assert "/" not in sanitize_for_path("path/to/file")
        assert "\\" not in sanitize_for_path("path\\to\\file")

    def test_null_byte(self):
        assert "\x00" not in sanitize_for_path("file\x00.txt")
