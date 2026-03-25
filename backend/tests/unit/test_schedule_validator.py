"""Unit tests for schedule validator module."""

from datetime import date

import pytest

from backend.app.models import CIFLocation, CIFSchedule, STPIndicator
from backend.app.services.schedule_validator import (
    apply_stp_overlays,
    expand_date_range,
    filter_schedules,
    get_departure_at_station,
    get_stop_type_at_station,
    schedule_runs_on_date,
)


def _make_schedule(
    uid: str = "A12345",
    date_from: date = date(2026, 3, 1),
    date_to: date = date(2026, 3, 31),
    days: str = "1111111",
    stp: STPIndicator = STPIndicator.PERMANENT,
    atoc: str = "VT",
    locations: list[CIFLocation] | None = None,
) -> CIFSchedule:
    if locations is None:
        locations = [
            CIFLocation(record_type="LO", tiploc="EUSTON", scheduled_departure="0830", public_departure="0830"),
            CIFLocation(record_type="LI", tiploc="RUGBY", scheduled_arrival="0920", scheduled_departure="0922",
                       public_arrival="0920", public_departure="0922", activity="T "),
            CIFLocation(record_type="LT", tiploc="BHAMNWS", scheduled_arrival="1015", public_arrival="1015"),
        ]
    return CIFSchedule(
        train_uid=uid,
        date_runs_from=date_from,
        date_runs_to=date_to,
        days_run=days,
        stp_indicator=stp,
        atoc_code=atoc,
        locations=locations,
    )


class TestScheduleRunsOnDate:
    def test_within_range_matching_day(self):
        sched = _make_schedule(days="1111111")  # Every day
        assert schedule_runs_on_date(sched, date(2026, 3, 15)) is True

    def test_before_range(self):
        sched = _make_schedule()
        assert schedule_runs_on_date(sched, date(2026, 2, 28)) is False

    def test_after_range(self):
        sched = _make_schedule()
        assert schedule_runs_on_date(sched, date(2026, 4, 1)) is False

    def test_day_mask_weekday_only(self):
        sched = _make_schedule(days="1111100")  # Mon-Fri
        # 2026-03-15 is a Sunday
        assert schedule_runs_on_date(sched, date(2026, 3, 15)) is False
        # 2026-03-16 is a Monday
        assert schedule_runs_on_date(sched, date(2026, 3, 16)) is True

    def test_day_mask_weekend_only(self):
        sched = _make_schedule(days="0000011")  # Sat-Sun
        assert schedule_runs_on_date(sched, date(2026, 3, 14)) is True  # Saturday
        assert schedule_runs_on_date(sched, date(2026, 3, 16)) is False  # Monday


class TestApplySTPOverlays:
    def test_permanent_only(self):
        schedules = [_make_schedule(stp=STPIndicator.PERMANENT)]
        result = apply_stp_overlays(schedules, date(2026, 3, 10))
        assert len(result) == 1
        assert result[0].stp_indicator == STPIndicator.PERMANENT

    def test_cancellation_removes(self):
        p = _make_schedule(stp=STPIndicator.PERMANENT)
        c = _make_schedule(stp=STPIndicator.CANCELLATION)
        result = apply_stp_overlays([p, c], date(2026, 3, 10))
        assert len(result) == 0  # Cancelled

    def test_overlay_replaces_permanent(self):
        p = _make_schedule(stp=STPIndicator.PERMANENT)
        o = _make_schedule(stp=STPIndicator.OVERLAY)
        result = apply_stp_overlays([p, o], date(2026, 3, 10))
        assert len(result) == 1
        assert result[0].stp_indicator == STPIndicator.OVERLAY

    def test_new_added_alongside_permanent(self):
        p = _make_schedule(uid="A12345", stp=STPIndicator.PERMANENT)
        n = _make_schedule(uid="A12345", stp=STPIndicator.NEW)
        result = apply_stp_overlays([p, n], date(2026, 3, 10))
        assert len(result) == 2  # Both included

    def test_different_uids_independent(self):
        s1 = _make_schedule(uid="A11111", stp=STPIndicator.PERMANENT)
        s2 = _make_schedule(uid="B22222", stp=STPIndicator.PERMANENT)
        result = apply_stp_overlays([s1, s2], date(2026, 3, 10))
        assert len(result) == 2


class TestFilterSchedules:
    def test_filter_by_operator(self):
        s1 = _make_schedule(atoc="VT")
        s2 = _make_schedule(atoc="GW")
        result = filter_schedules([s1, s2], operator_code="VT")
        assert len(result) == 1
        assert result[0].atoc_code == "VT"

    def test_filter_by_station(self):
        s1 = _make_schedule()
        result = filter_schedules([s1], station_tiploc="RUGBY")
        assert len(result) == 1

    def test_filter_by_station_not_found(self):
        s1 = _make_schedule()
        result = filter_schedules([s1], station_tiploc="PADDTON")
        assert len(result) == 0


class TestGetDepartureAtStation:
    def test_origin_departure(self):
        sched = _make_schedule()
        dep = get_departure_at_station(sched, "EUSTON")
        assert dep == "0830"

    def test_intermediate_departure(self):
        sched = _make_schedule()
        dep = get_departure_at_station(sched, "RUGBY")
        assert dep == "0922"

    def test_terminus_no_departure(self):
        sched = _make_schedule()
        dep = get_departure_at_station(sched, "BHAMNWS")
        assert dep is None  # Terminus has no departure

    def test_unknown_station(self):
        sched = _make_schedule()
        dep = get_departure_at_station(sched, "UNKNOWN")
        assert dep is None

    def test_pass_through_returns_pass_time(self):
        """Pass-through LI records return their scheduled pass time as the departure."""
        locations = [
            CIFLocation(record_type="LO", tiploc="WOKING", scheduled_departure="0836", public_departure="0836"),
            # Pass-through: only sched_pass, no public times, no stop activity
            CIFLocation(record_type="LI", tiploc="FARNBRG", scheduled_arrival="0845", scheduled_departure="0845",
                        activity="   "),
            CIFLocation(record_type="LT", tiploc="BASNGSK", scheduled_arrival="0900", public_arrival="0900"),
        ]
        sched = _make_schedule(locations=locations)
        dep = get_departure_at_station(sched, "FARNBRG")
        assert dep == "0845"  # Pass time is returned


class TestGetStopTypeAtStation:
    def _make_pass_through_schedule(self):
        locations = [
            CIFLocation(record_type="LO", tiploc="WOKING", scheduled_departure="0836", public_departure="0836"),
            CIFLocation(record_type="LI", tiploc="FARNBRG", scheduled_arrival="0845", scheduled_departure="0845",
                        activity="   "),
            CIFLocation(record_type="LT", tiploc="BASNGSK", scheduled_arrival="0900", public_arrival="0900"),
        ]
        return _make_schedule(locations=locations)

    def test_origin_is_stop(self):
        sched = _make_schedule()
        assert get_stop_type_at_station(sched, "EUSTON") == "stop"

    def test_intermediate_stop_is_stop(self):
        sched = _make_schedule()
        assert get_stop_type_at_station(sched, "RUGBY") == "stop"

    def test_terminus_is_stop(self):
        sched = _make_schedule()
        assert get_stop_type_at_station(sched, "BHAMNWS") == "stop"

    def test_pass_through_is_pass(self):
        sched = self._make_pass_through_schedule()
        assert get_stop_type_at_station(sched, "FARNBRG") == "pass"

    def test_unknown_station_is_none(self):
        sched = _make_schedule()
        assert get_stop_type_at_station(sched, "UNKNOWN") is None


class TestExpandDateRange:
    def test_single_day(self):
        dates = expand_date_range(date(2026, 3, 15), date(2026, 3, 15))
        assert dates == [date(2026, 3, 15)]

    def test_multi_day(self):
        dates = expand_date_range(date(2026, 3, 1), date(2026, 3, 3))
        assert len(dates) == 3
        assert dates[0] == date(2026, 3, 1)
        assert dates[2] == date(2026, 3, 3)
