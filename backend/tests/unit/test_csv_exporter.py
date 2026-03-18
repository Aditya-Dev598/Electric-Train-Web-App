"""Unit tests for CSV exporter with injection protection."""

import pytest

from backend.app.models import RouteRow, TimetableRow
from backend.app.services.csv_exporter import (
    generate_route_csv,
    generate_timetable_csv,
    sanitize_csv_value,
)


class TestSanitizeCSVValue:
    def test_normal_value(self):
        assert sanitize_csv_value("hello") == "hello"

    def test_empty(self):
        assert sanitize_csv_value("") == ""

    def test_equals_prefix(self):
        assert sanitize_csv_value("=cmd()") == "'=cmd()"

    def test_plus_prefix(self):
        assert sanitize_csv_value("+cmd()") == "'+cmd()"

    def test_minus_prefix(self):
        assert sanitize_csv_value("-cmd()") == "'-cmd()"

    def test_at_prefix(self):
        assert sanitize_csv_value("@cmd()") == "'@cmd()"

    def test_tab_prefix(self):
        assert sanitize_csv_value("\tcmd()") == "'\tcmd()"

    def test_number_ok(self):
        assert sanitize_csv_value("123.45") == "123.45"


class TestGenerateTimetableCSV:
    def test_empty(self):
        csv = generate_timetable_csv([])
        lines = csv.strip().split("\n")
        assert len(lines) == 1  # Header only
        assert "date" in lines[0]
        assert "departure_time" in lines[0]

    def test_with_rows(self):
        rows = [
            TimetableRow(
                date="2026-03-15",
                departure_time="08:30:00",
                train_route="TestRoute",
                train_class="Standard",
                number_of_coaches="8",
            ),
            TimetableRow(
                date="2026-03-15",
                departure_time="09:00:00",
                train_route="TestRoute",
                train_class="",
                number_of_coaches="",
            ),
        ]
        csv = generate_timetable_csv(rows)
        lines = csv.strip().split("\n")
        assert len(lines) == 3  # Header + 2 rows
        assert "2026-03-15" in lines[1]
        assert "08:30:00" in lines[1]

    def test_injection_protection(self):
        rows = [
            TimetableRow(
                date="2026-03-15",
                departure_time="08:30:00",
                train_route="=HYPERLINK()",
                train_class="",
                number_of_coaches="",
            ),
        ]
        csv = generate_timetable_csv(rows)
        assert "'=HYPERLINK()" in csv


class TestGenerateRouteCSV:
    def test_empty(self):
        csv = generate_route_csv([])
        lines = csv.strip().split("\n")
        assert len(lines) == 1

    def test_with_rows(self):
        rows = [
            RouteRow(
                route_variant="Stopping",
                seq=1,
                from_station="KGX",
                to_station="PBO",
                distance_miles="76.42",
                run_min="47",
                wait_min="0",
            ),
        ]
        csv = generate_route_csv(rows)
        lines = csv.strip().split("\n")
        assert len(lines) == 2
        assert "KGX" in lines[1]
        assert "76.42" in lines[1]
