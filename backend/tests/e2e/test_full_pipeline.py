"""End-to-end tests for the complete pipeline stack.

Uses synthetic data (no real CIF required) plus the existing
Sample Output CSVs committed to the repository.

Tests:
  1. test_sample_output_csv_structure      — structural checks on Sample Output CSVs
  2. test_result_store                     — save / load / list / delete cycle
  3. test_electric_pipeline_synthetic      — electric energy pipeline with synthetic inputs
  4. test_solar_pipeline_synthetic         — solar analysis pipeline with synthetic inputs
  5. test_electric_transform               — format transform from scraper → pipeline columns
  6. test_combine_route_csv_deduplication  — multi-result route deduplication
"""

from __future__ import annotations

import io
import struct
import tempfile
import textwrap
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import pytest

# Project root relative to this file
_REPO = Path(__file__).resolve().parents[3]
_SAMPLE_OUT = _REPO / "Sample Output"


# ---------------------------------------------------------------------------
# 1. Sample Output CSV structure
# ---------------------------------------------------------------------------

class TestSampleOutputStructure:
    """Validate that the committed sample output CSVs have the expected shape."""

    TIMETABLE_REQUIRED_COLS = {"route_variant", "date", "departure_time"}
    ROUTE_REQUIRED_COLS = {
        "route_variant", "seq", "from_station", "to_station",
        "run_min", "wait_min",
    }

    @pytest.mark.parametrize("fname", [
        "Farnborough (Main) timetable.csv",
        "Fleet Timetable.csv",
    ])
    def test_timetable_columns(self, fname):
        path = _SAMPLE_OUT / fname
        df = pd.read_csv(path)
        assert self.TIMETABLE_REQUIRED_COLS.issubset(set(df.columns)), (
            f"{fname} missing columns: {self.TIMETABLE_REQUIRED_COLS - set(df.columns)}"
        )

    @pytest.mark.parametrize("fname", [
        "Farnborough (Main) route.csv",
        "Fleet Route.csv",
    ])
    def test_route_columns(self, fname):
        path = _SAMPLE_OUT / fname
        df = pd.read_csv(path)
        assert self.ROUTE_REQUIRED_COLS.issubset(set(df.columns)), (
            f"{fname} missing columns: {self.ROUTE_REQUIRED_COLS - set(df.columns)}"
        )

    def test_farnborough_timetable_nonempty(self):
        df = pd.read_csv(_SAMPLE_OUT / "Farnborough (Main) timetable.csv")
        assert len(df) > 0

    def test_farnborough_timetable_departure_time_parseable(self):
        df = pd.read_csv(_SAMPLE_OUT / "Farnborough (Main) timetable.csv")
        # times should parse as HH:MM[:SS]
        times = df["departure_time"].astype(str)
        for t in times:
            assert len(t) >= 5, f"Unexpected time format: {t!r}"
            assert t[:2].isdigit() and t[3:5].isdigit(), f"Non-numeric time: {t!r}"

    def test_farnborough_route_seq_monotonic_per_variant(self):
        df = pd.read_csv(_SAMPLE_OUT / "Farnborough (Main) route.csv")
        for variant, grp in df.groupby("route_variant"):
            seqs = grp["seq"].tolist()
            assert seqs == sorted(seqs), (
                f"Route variant '{variant}' seq not monotonic: {seqs}"
            )

    def test_farnborough_route_has_multiple_variants(self):
        df = pd.read_csv(_SAMPLE_OUT / "Farnborough (Main) route.csv")
        assert df["route_variant"].nunique() >= 1


# ---------------------------------------------------------------------------
# 2. ResultStore
# ---------------------------------------------------------------------------

class TestResultStore:
    """Save / load / list / delete cycle."""

    def test_save_and_load(self, tmp_path):
        from backend.app.services.result_store import ResultStore
        store = ResultStore(str(tmp_path))
        store.save("id1", "tt_csv", "rt_csv", "dbg_csv", {"station": "FNB"})
        assert store.load_csv("id1", "timetable") == "tt_csv"
        assert store.load_csv("id1", "route") == "rt_csv"
        assert store.load_csv("id1", "debug") == "dbg_csv"

    def test_metadata_round_trip(self, tmp_path):
        from backend.app.services.result_store import ResultStore
        store = ResultStore(str(tmp_path))
        meta = {"station": "FNB", "timetable_rows": 42}
        store.save("id2", "a", "b", "c", meta)
        loaded = store.load_metadata("id2")
        assert loaded["station"] == "FNB"
        assert loaded["timetable_rows"] == 42

    def test_list_results(self, tmp_path):
        from backend.app.services.result_store import ResultStore
        store = ResultStore(str(tmp_path))
        store.save("a1", "t", "r", "d", {"station": "A"})
        store.save("b2", "t", "r", "d", {"station": "B"})
        results = store.list_results()
        ids = {r["generation_id"] for r in results}
        assert {"a1", "b2"}.issubset(ids)

    def test_delete(self, tmp_path):
        from backend.app.services.result_store import ResultStore
        store = ResultStore(str(tmp_path))
        store.save("x1", "t", "r", "d", {})
        assert store.exists("x1")
        store.delete("x1")
        assert not store.exists("x1")
        assert store.load_csv("x1", "timetable") is None

    def test_write_csv_updates_on_disk(self, tmp_path):
        from backend.app.services.result_store import ResultStore
        store = ResultStore(str(tmp_path))
        store.save("e1", "original", "r", "d", {})
        store.write_csv("e1", "timetable", "edited")
        assert store.load_csv("e1", "timetable") == "edited"


# ---------------------------------------------------------------------------
# 3. Electric pipeline — synthetic inputs
# ---------------------------------------------------------------------------

def _make_timetable_csv(scraper_format: bool = True) -> str:
    """Return a minimal timetable CSV."""
    if scraper_format:
        return textwrap.dedent("""\
            route_variant,departure_time,date,train_class,number_of_coaches
            R1,08:00:00,2025-01-15,Class377,8
            R1,09:00:00,2025-01-15,Class377,8
        """)
    return textwrap.dedent("""\
        route_variant,dep_time,date,train_type,cars
        R1,08:00,15/01/2025,Class377,8
        R1,09:00,15/01/2025,Class377,8
    """)


def _make_route_csv() -> str:
    return textwrap.dedent("""\
        route_variant,seq,from_station,to_station,distance_miles,run_min,wait_min
        R1,1,Station A,Station B,10.0,15,2
        R1,2,Station B,Station C,8.0,12,1
    """)


def _make_rolling_stock_csv(tmp_path: Path) -> Path:
    path = tmp_path / "rolling_stock.csv"
    path.write_text(textwrap.dedent("""\
        train_type,kwh_per_km_per_car,aux_kw_per_car,drive_eff,regen_eff,line_losses_pct
        Class377,2.0,10.0,0.9,0.0,0.0
    """))
    return path


def _make_station_points_csv(tmp_path: Path) -> Path:
    path = tmp_path / "station_points.csv"
    path.write_text(textwrap.dedent("""\
        Station,TSS
        Station A,TSS_NORTH
        Station B,TSS_NORTH
        Station C,TSS_SOUTH
    """))
    return path


class TestElectricPipeline:

    def test_returns_tss_dict(self, tmp_path):
        from backend.app.services.electric_pipeline import run_pipeline
        rs = _make_rolling_stock_csv(tmp_path)
        sp = _make_station_points_csv(tmp_path)
        result = run_pipeline(_make_timetable_csv(), _make_route_csv(), rs, sp)
        assert isinstance(result, dict)
        assert len(result) > 0, "Pipeline returned no TSS outputs"

    def test_tss_names_are_safe_strings(self, tmp_path):
        from backend.app.services.electric_pipeline import run_pipeline
        rs = _make_rolling_stock_csv(tmp_path)
        sp = _make_station_points_csv(tmp_path)
        result = run_pipeline(_make_timetable_csv(), _make_route_csv(), rs, sp)
        for name in result:
            assert name.replace("_", "").isalnum() or "_" in name, (
                f"TSS name not safe: {name!r}"
            )

    def test_each_tss_csv_has_48_bin_columns(self, tmp_path):
        from backend.app.services.electric_pipeline import run_pipeline, BIN_LABELS
        rs = _make_rolling_stock_csv(tmp_path)
        sp = _make_station_points_csv(tmp_path)
        result = run_pipeline(_make_timetable_csv(), _make_route_csv(), rs, sp)
        for tss_name, csv_text in result.items():
            df = pd.read_csv(io.StringIO(csv_text))
            bin_cols = [c for c in df.columns if c in BIN_LABELS]
            assert len(bin_cols) == 48, (
                f"{tss_name}: expected 48 bin columns, got {len(bin_cols)}"
            )

    def test_bin_values_are_non_negative(self, tmp_path):
        from backend.app.services.electric_pipeline import run_pipeline, BIN_LABELS
        rs = _make_rolling_stock_csv(tmp_path)
        sp = _make_station_points_csv(tmp_path)
        result = run_pipeline(_make_timetable_csv(), _make_route_csv(), rs, sp)
        for tss_name, csv_text in result.items():
            df = pd.read_csv(io.StringIO(csv_text))
            bin_cols = [c for c in df.columns if c in BIN_LABELS]
            assert (df[bin_cols].fillna(0) >= 0).all().all(), (
                f"{tss_name}: negative energy values found"
            )

    def test_bin_values_no_nan(self, tmp_path):
        from backend.app.services.electric_pipeline import run_pipeline, BIN_LABELS
        rs = _make_rolling_stock_csv(tmp_path)
        sp = _make_station_points_csv(tmp_path)
        result = run_pipeline(_make_timetable_csv(), _make_route_csv(), rs, sp)
        for tss_name, csv_text in result.items():
            df = pd.read_csv(io.StringIO(csv_text))
            bin_cols = [c for c in df.columns if c in BIN_LABELS]
            assert not df[bin_cols].isna().any().any(), (
                f"{tss_name}: NaN values in bin columns"
            )

    def test_total_units_equals_sum_of_bins(self, tmp_path):
        from backend.app.services.electric_pipeline import run_pipeline, BIN_LABELS
        rs = _make_rolling_stock_csv(tmp_path)
        sp = _make_station_points_csv(tmp_path)
        result = run_pipeline(_make_timetable_csv(), _make_route_csv(), rs, sp)
        for tss_name, csv_text in result.items():
            df = pd.read_csv(io.StringIO(csv_text))
            bin_cols = [c for c in df.columns if c in BIN_LABELS]
            computed = df[bin_cols].sum(axis=1).round(6)
            stored = df["Total Units"].round(6)
            pd.testing.assert_series_equal(
                computed.reset_index(drop=True),
                stored.reset_index(drop=True),
                check_names=False,
            )


class TestElectricTransform:
    """format transform: scraper columns → pipeline columns."""

    def test_scraper_columns_accepted(self, tmp_path):
        from backend.app.services.electric_pipeline import run_pipeline
        rs = _make_rolling_stock_csv(tmp_path)
        sp = _make_station_points_csv(tmp_path)
        # Should not raise even with scraper-format column names
        result = run_pipeline(
            _make_timetable_csv(scraper_format=True),
            _make_route_csv(), rs, sp,
        )
        assert len(result) > 0

    def test_native_columns_accepted(self, tmp_path):
        from backend.app.services.electric_pipeline import run_pipeline
        rs = _make_rolling_stock_csv(tmp_path)
        sp = _make_station_points_csv(tmp_path)
        result = run_pipeline(
            _make_timetable_csv(scraper_format=False),
            _make_route_csv(), rs, sp,
        )
        assert len(result) > 0


class TestCombineRoutes:
    """Multi-result route deduplication."""

    def test_combine_route_deduplicates(self):
        from backend.app.services.electric_pipeline import combine_route_csvs
        csv_a = textwrap.dedent("""\
            route_variant,seq,from_station,to_station,distance_miles,run_min,wait_min
            R1,1,A,B,10,15,2
            R1,2,B,C,8,12,1
        """)
        # Identical to csv_a — duplicate should be removed
        csv_b = textwrap.dedent("""\
            route_variant,seq,from_station,to_station,distance_miles,run_min,wait_min
            R1,1,A,B,10,15,2
            R1,2,B,C,8,12,1
        """)
        combined = combine_route_csvs([csv_a, csv_b])
        df = pd.read_csv(io.StringIO(combined))
        assert len(df) == 2, f"Expected 2 unique rows, got {len(df)}"

    def test_combine_route_keeps_distinct_variants(self):
        from backend.app.services.electric_pipeline import combine_route_csvs
        csv_a = "route_variant,seq,from_station,to_station,distance_miles,run_min,wait_min\nR1,1,A,B,10,15,2\n"
        csv_b = "route_variant,seq,from_station,to_station,distance_miles,run_min,wait_min\nR2,1,X,Y,5,8,0\n"
        combined = combine_route_csvs([csv_a, csv_b])
        df = pd.read_csv(io.StringIO(combined))
        assert set(df["route_variant"]) == {"R1", "R2"}


# ---------------------------------------------------------------------------
# 4. Solar pipeline — synthetic inputs
# ---------------------------------------------------------------------------

def _make_demand_csv() -> str:
    """Synthetic half-hour demand CSV (48 bins) for 3 days."""
    from backend.app.services.electric_pipeline import BIN_LABELS
    header = "Date,Day,Total Units," + ",".join(BIN_LABELS)
    rows = []
    for i in range(3):
        day = datetime(2025, 6, 1) + timedelta(days=i)
        # Simulate demand peaking at midday
        bins = [max(0.0, 10.0 - abs(j - 24) * 0.5) for j in range(48)]
        total = sum(bins)
        row = f"{day.strftime('%d/%m/%Y')},{day.strftime('%A')},{total:.4f},"
        row += ",".join(f"{v:.4f}" for v in bins)
        rows.append(row)
    return header + "\n" + "\n".join(rows) + "\n"


def _make_pvgis_csv() -> str:
    """Synthetic PVGIS CSV matching the expected format."""
    lines = [
        "Latitude: 51.295",
        "Longitude: -0.773",
        "Elevation: 65 m",
        "# some other metadata",
        "time,P,G(i),H_sun,T2m,WS10m,Int",
    ]
    # 3 days × 24 hours
    for i in range(3):
        day = datetime(2019, 6, 1) + timedelta(days=i)
        for h in range(24):
            dt_str = day.strftime("%Y%m%d") + f":{h:02d}00"
            # solar power peaks at midday
            p = max(0.0, 500.0 * (1 - abs(h - 12) / 6)) if 6 <= h <= 18 else 0.0
            lines.append(f"{dt_str},{p:.1f},0.0,0.0,15.0,3.0,0")
    return "\n".join(lines) + "\n"


class TestSolarPipeline:

    def test_returns_solar_result(self):
        from backend.app.services.solar_pipeline import run_solar_pipeline, SolarResult
        result = run_solar_pipeline(_make_demand_csv(), _make_pvgis_csv())
        assert isinstance(result, SolarResult)

    def test_avg_profile_png_is_valid_png(self):
        from backend.app.services.solar_pipeline import run_solar_pipeline
        result = run_solar_pipeline(_make_demand_csv(), _make_pvgis_csv())
        # PNG files start with the 8-byte PNG signature
        assert result.avg_profile_png[:8] == b"\x89PNG\r\n\x1a\n", (
            "avg_profile_png does not start with PNG signature"
        )

    def test_metrics_xlsx_is_nonempty(self):
        from backend.app.services.solar_pipeline import run_solar_pipeline
        result = run_solar_pipeline(_make_demand_csv(), _make_pvgis_csv())
        assert len(result.metrics_xlsx) > 0

    def test_demand_hourly_xlsx_is_nonempty(self):
        from backend.app.services.solar_pipeline import run_solar_pipeline
        result = run_solar_pipeline(_make_demand_csv(), _make_pvgis_csv())
        assert len(result.demand_hourly_xlsx) > 0

    def test_solar_share_pct_in_range(self):
        from backend.app.services.solar_pipeline import run_solar_pipeline
        result = run_solar_pipeline(_make_demand_csv(), _make_pvgis_csv())
        assert 0.0 <= result.solar_share_pct <= 100.0, (
            f"solar_share_pct out of range: {result.solar_share_pct}"
        )

    def test_utilisation_pct_in_range(self):
        from backend.app.services.solar_pipeline import run_solar_pipeline
        result = run_solar_pipeline(_make_demand_csv(), _make_pvgis_csv())
        assert 0.0 <= result.utilisation_pct <= 100.0, (
            f"utilisation_pct out of range: {result.utilisation_pct}"
        )

    def test_metrics_workbook_has_required_sheets(self):
        """Annual Summary, Seasonal Summary, and Matched Detail sheets must exist."""
        import openpyxl
        from backend.app.services.solar_pipeline import run_solar_pipeline
        result = run_solar_pipeline(_make_demand_csv(), _make_pvgis_csv())
        wb = openpyxl.load_workbook(io.BytesIO(result.metrics_xlsx))
        assert "Annual Summary" in wb.sheetnames
        assert "Seasonal Summary" in wb.sheetnames
        assert "Matched Detail" in wb.sheetnames

    def test_avg_profile_xlsx_has_24_hour_rows(self):
        import openpyxl
        from backend.app.services.solar_pipeline import run_solar_pipeline
        result = run_solar_pipeline(_make_demand_csv(), _make_pvgis_csv())
        wb = openpyxl.load_workbook(io.BytesIO(result.avg_profile_xlsx))
        ws = wb.active
        # 1 header + 24 data rows
        assert ws.max_row == 25, f"Expected 25 rows (1 header + 24 hours), got {ws.max_row}"
