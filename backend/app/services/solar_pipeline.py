"""Solar pipeline service.

Ported from solar_pipeline_gui.py (Aditya-Dev598/Electric-Train) with the
Tkinter GUI removed and replaced by pure in-memory I/O for use as a web service.

Key changes vs. the original:
  - All file-path inputs replaced by CSV text strings / DataFrames.
  - XLSX and PNG outputs returned as bytes (BytesIO) instead of written to disk.
  - matplotlib backend forced to 'Agg' (headless, no display required).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")  # headless backend — must be set before importing pyplot
import matplotlib.pyplot as plt
import pandas as pd


# ---------------------------------------------------------------------------
# Dataclass returned by run_solar_pipeline()
# ---------------------------------------------------------------------------

@dataclass
class SolarResult:
    demand_hourly_xlsx: bytes   # demand_hourly_wide.xlsx
    pvgis_supply_xlsx: bytes    # pvgis_supply_hourly_wide.xlsx
    avg_profile_xlsx: bytes     # avg_demand_supply_usedsolar_24h.xlsx
    avg_profile_png: bytes      # avg_demand_supply_usedsolar_24h.png
    metrics_xlsx: bytes         # solar_metrics_summary.xlsx (annual + seasonal)
    solar_share_pct: float      # annual solar share % (for API response)
    utilisation_pct: float      # annual utilisation % (for API response)
    seasonal_chart_png: bytes   # bar chart: solar yield by 4 seasons
    daytype_chart_png: bytes    # line chart: traction demand by day type


# ---------------------------------------------------------------------------
# Helpers (ported verbatim from solar_pipeline_gui.py)
# ---------------------------------------------------------------------------

TIME_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\s*$")


def _parse_time_to_hhmm(col) -> str | None:
    if hasattr(col, "hour") and hasattr(col, "minute"):
        return f"{col.hour:02d}:{col.minute:02d}"
    if isinstance(col, pd.Timedelta):
        total_seconds = int(col.total_seconds())
        h = (total_seconds // 3600) % 24
        m = (total_seconds % 3600) // 60
        return f"{h:02d}:{m:02d}"
    if isinstance(col, str):
        m = TIME_RE.match(col.strip())
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2))
            return f"{hh:02d}:{mm:02d}"
    return None


def _parse_hour_label(col) -> str | None:
    hhmm = _parse_time_to_hhmm(col)
    if hhmm and hhmm.endswith(":00"):
        return hhmm
    return None


def _parse_date_series(s: pd.Series) -> pd.Series:
    dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
    if dt.isna().mean() > 0.2:
        dt2 = pd.to_datetime(s, errors="coerce")
        dt = dt.fillna(dt2)
    return dt


def _normalize_hour_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for c in df.columns:
        lab = _parse_hour_label(c)
        if lab is not None:
            rename[c] = lab
    out = df.copy().rename(columns=rename)
    out.columns = [str(c).strip() for c in out.columns]
    return out


def _ensure_24_hours(df: pd.DataFrame, who: str) -> list[str]:
    hour_cols = [f"{h:02d}:00" for h in range(24)]
    missing = [c for c in hour_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{who}: missing hour columns after normalisation: {missing}")
    return hour_cols


def _season_label(month: int) -> str:
    """Four-season label used for the seasonal chart (does not affect metrics_xlsx)."""
    if month in (12, 1, 2): return "Winter"
    if month in (3, 4, 5):  return "Spring"
    if month in (6, 7, 8):  return "Summer"
    return "Autumn"


def _season_from_month(month: int) -> str:
    if month in (12, 1, 2):
        return "DJF"
    if month in (6, 7, 8):
        return "JJA"
    return "SHOULDER"


# ---------------------------------------------------------------------------
# Step 1: half-hour demand CSV → hourly wide DataFrame
# ---------------------------------------------------------------------------

def hh_wide_to_hourly_wide(demand_csv: str) -> pd.DataFrame:
    """Convert a half-hour TSS demand CSV (48 columns) to 24-column hourly wide format."""
    df = pd.read_csv(io.StringIO(demand_csv))

    # Find the date column
    date_col = next(
        (c for c in df.columns if isinstance(c, str) and "date" in c.lower()),
        df.columns[0],
    )

    # Map half-hour column labels → HH:MM
    time_map = {}
    for c in df.columns:
        if c == date_col:
            continue
        hhmm = _parse_time_to_hhmm(c)
        if hhmm is not None:
            time_map[c] = hhmm

    if len(time_map) < 40:
        raise ValueError(
            f"Could not detect enough time columns in demand CSV (found {len(time_map)}, need ≥ 40). "
            "Expected 48 half-hour columns named e.g. '0:30', '1:00' … '0:00'."
        )

    hh = df[[date_col] + list(time_map.keys())].rename(columns=time_map).copy()
    hh[date_col] = pd.to_datetime(hh[date_col], dayfirst=True, errors="coerce")
    if hh[date_col].isna().mean() > 0.2:
        raise ValueError(f"Date parsing failed for column '{date_col}'.")

    for c in hh.columns:
        if c != date_col:
            hh[c] = pd.to_numeric(hh[c], errors="coerce").fillna(0)

    out = pd.DataFrame()
    out["Date"] = hh[date_col].dt.strftime("%d/%m/%Y")
    hour_cols = []
    for h in range(24):
        t00 = f"{h:02d}:00"
        t30 = f"{h:02d}:30"
        out[t00] = (hh[t00] if t00 in hh.columns else 0) + (hh[t30] if t30 in hh.columns else 0)
        hour_cols.append(t00)

    out["Total Units"] = out[hour_cols].sum(axis=1)
    return out[["Date", "Total Units"] + hour_cols]


# ---------------------------------------------------------------------------
# Step 2: PVGIS CSV → hourly wide DataFrame
# ---------------------------------------------------------------------------

def clean_pvgis_to_wide_hourly(pvgis_csv: str) -> pd.DataFrame:
    """Parse PVGIS hourly CSV and convert to 24-column wide format keyed by date."""
    lines = pvgis_csv.splitlines()
    header_row = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("time,"):
            header_row = i
            break

    if header_row is None:
        raise ValueError(
            "Could not find PVGIS table header (line starting with 'time,'). "
            "Download the hourly data CSV from PVGIS and upload it unchanged."
        )

    df = pd.read_csv(io.StringIO(pvgis_csv), skiprows=header_row)
    if "time" not in df.columns or "P" not in df.columns:
        raise ValueError(
            f"Expected PVGIS columns 'time' and 'P'. Found: {df.columns.tolist()}"
        )

    dt = pd.to_datetime(
        df["time"].astype(str).str.strip(), format="%Y%m%d:%H%M", errors="coerce"
    )
    if dt.isna().mean() > 0.1:
        raise ValueError(
            "Failed to parse PVGIS 'time' column with format %Y%m%d:%H%M. "
            "Ensure you are using the PVGIS hourly radiation download."
        )

    out = pd.DataFrame({
        "datetime": dt,
        "kW": pd.to_numeric(df["P"], errors="coerce").fillna(0) / 1000.0,
    })
    out["datetime"] = out["datetime"].dt.floor("h")
    out = out.groupby("datetime", as_index=False)["kW"].mean()
    out["DateKey"] = out["datetime"].dt.normalize()
    out["Hour"] = out["datetime"].dt.strftime("%H:%M")

    wide = out.pivot_table(index="DateKey", columns="Hour", values="kW", aggfunc="mean")
    hour_cols = [f"{h:02d}:00" for h in range(24)]
    wide = wide.reindex(columns=hour_cols).fillna(0)

    final = pd.DataFrame()
    final["Date"] = wide.index.strftime("%d/%m/%Y")
    final["Total Units"] = wide[hour_cols].sum(axis=1)
    for c in hour_cols:
        final[c] = wide[c].values
    return final[["Date", "Total Units"] + hour_cols]


# ---------------------------------------------------------------------------
# Step 3: average profile + plot (returns bytes)
# ---------------------------------------------------------------------------

def _build_supply_md_latest(supply_df: pd.DataFrame, hour_cols: list[str]) -> pd.DataFrame:
    supply = supply_df[["Date"] + hour_cols].copy()
    supply["Date_dt"] = _parse_date_series(supply["Date"])
    if supply["Date_dt"].isna().mean() > 0.2:
        raise ValueError("Could not parse supply Date reliably.")
    supply["md"] = supply["Date_dt"].dt.strftime("%m-%d")
    for c in hour_cols:
        supply[c] = pd.to_numeric(supply[c], errors="coerce").fillna(0)
    supply["_year"] = supply["Date_dt"].dt.year
    return (
        supply.sort_values(["md", "_year"], ascending=[True, False])
        .drop_duplicates("md", keep="first")
        .drop(columns=["_year"])
    )


def _match_supply_to_demand(
    demand_df: pd.DataFrame, supply_df: pd.DataFrame, hour_cols: list[str]
) -> pd.DataFrame:
    demand = demand_df[["Date"] + hour_cols].copy()
    demand["Date_dt"] = _parse_date_series(demand["Date"])
    if demand["Date_dt"].isna().mean() > 0.2:
        raise ValueError("Could not parse demand Date reliably.")
    demand["md"] = demand["Date_dt"].dt.strftime("%m-%d")
    for c in hour_cols:
        demand[c] = pd.to_numeric(demand[c], errors="coerce").fillna(0)

    supply_md = _build_supply_md_latest(supply_df, hour_cols)
    merged = demand.merge(
        supply_md[["md"] + hour_cols],
        on="md",
        how="left",
        suffixes=("_demand", "_supply"),
    )
    supply_cols = [f"{c}_supply" for c in hour_cols]
    if merged[supply_cols].isna().all(axis=1).any():
        missing = merged.loc[merged[supply_cols].isna().all(axis=1), "md"].unique().tolist()
        raise ValueError(f"Supply data missing for month-day keys: {missing[:10]}")
    merged[supply_cols] = merged[supply_cols].fillna(0)
    return merged


def build_average_profile(
    demand_df: pd.DataFrame, supply_df: pd.DataFrame
) -> tuple[pd.DataFrame, bytes, bytes]:
    """Return (profile_df, png_bytes, xlsx_bytes)."""
    d_df = _normalize_hour_columns(demand_df)
    s_df = _normalize_hour_columns(supply_df)

    if "Date" not in d_df.columns or "Date" not in s_df.columns:
        raise ValueError("Both demand and supply DataFrames must have a 'Date' column.")

    hour_cols = _ensure_24_hours(d_df, "DEMAND")
    _ensure_24_hours(s_df, "SUPPLY")

    merged = _match_supply_to_demand(d_df, s_df, hour_cols)

    avg_demand, avg_supply, used_solar_avg = [], [], []
    for c in hour_cols:
        d = merged[f"{c}_demand"].mean()
        s = merged[f"{c}_supply"].mean()
        avg_demand.append(d)
        avg_supply.append(s)
        used_solar_avg.append(min(d, s))

    profile = pd.DataFrame({
        "Hour": hour_cols,
        "Average Demand": avg_demand,
        "Average Supply": avg_supply,
        "Used Solar (min of averages)": used_solar_avg,
    })

    # XLSX output
    xlsx_buf = io.BytesIO()
    with pd.ExcelWriter(xlsx_buf, engine="openpyxl") as writer:
        profile.to_excel(writer, index=False)
    xlsx_bytes = xlsx_buf.getvalue()

    # PNG output
    x = list(range(24))
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(x, profile["Average Demand"], label="Average Demand", color="#e22a87", linewidth=2.5)
    ax.plot(x, profile["Average Supply"], label="Average Supply", color="#1e75bb", linewidth=2.5)
    ax.fill_between(x, profile["Used Solar (min of averages)"], color="#ffe784", alpha=0.6, label="Used Solar")
    ax.set_xlabel("Hour")
    ax.set_ylabel("Energy (same units as input files)")
    ax.set_xticks(x)
    ax.set_xticklabels(hour_cols, rotation=45, ha="right")
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left")
    plt.tight_layout()
    png_buf = io.BytesIO()
    plt.savefig(png_buf, format="png", dpi=150)
    plt.close(fig)
    png_bytes = png_buf.getvalue()

    return profile, png_bytes, xlsx_bytes


# ---------------------------------------------------------------------------
# Step 4: solar metrics workbook (returns bytes)
# ---------------------------------------------------------------------------

def compute_metrics(
    demand_df: pd.DataFrame, supply_df: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (annual_summary, seasonal_summary, detail) DataFrames."""
    d_df = _normalize_hour_columns(demand_df)
    s_df = _normalize_hour_columns(supply_df)

    hour_cols = [f"{h:02d}:00" for h in range(24)]
    if "Date" not in d_df.columns or "Date" not in s_df.columns:
        raise ValueError("Both DataFrames must have a 'Date' column.")

    demand = d_df[["Date"] + hour_cols].copy()
    supply = s_df[["Date"] + hour_cols].copy()
    demand["Date_dt"] = _parse_date_series(demand["Date"])
    supply["Date_dt"] = _parse_date_series(supply["Date"])

    if demand["Date_dt"].isna().mean() > 0.2:
        raise ValueError("Could not parse demand Date reliably.")
    if supply["Date_dt"].isna().mean() > 0.2:
        raise ValueError("Could not parse supply Date reliably.")

    demand["md"] = demand["Date_dt"].dt.strftime("%m-%d")
    supply["md"] = supply["Date_dt"].dt.strftime("%m-%d")

    for c in hour_cols:
        demand[c] = pd.to_numeric(demand[c], errors="coerce").fillna(0)
        supply[c] = pd.to_numeric(supply[c], errors="coerce").fillna(0)

    supply["_year"] = supply["Date_dt"].dt.year
    supply = (
        supply.sort_values(["md", "_year"], ascending=[True, False])
        .drop_duplicates("md", keep="first")
        .drop(columns=["_year"])
    )

    demand = demand.rename(columns={c: f"{c}_demand" for c in hour_cols})
    merged = demand.merge(supply[["md"] + hour_cols], on="md", how="left")
    if merged[hour_cols].isna().all(axis=1).any():
        missing = merged.loc[merged[hour_cols].isna().all(axis=1), "md"].unique().tolist()
        raise ValueError(f"No supply match for month-days: {missing[:10]}")

    merged = merged.rename(columns={c: f"{c}_supply" for c in hour_cols})
    used_cols = []
    for c in hour_cols:
        u = f"{c}_used"
        merged[u] = merged[[f"{c}_demand", f"{c}_supply"]].min(axis=1)
        used_cols.append(u)

    total_demand = merged[[f"{c}_demand" for c in hour_cols]].to_numpy().sum()
    total_supply = merged[[f"{c}_supply" for c in hour_cols]].to_numpy().sum()
    used_solar = merged[used_cols].to_numpy().sum()

    annual = pd.DataFrame([{
        "Demand Days (rows)": len(merged),
        "Supply Days Used (unique md)": merged["md"].nunique(),
        "Total Demand": total_demand,
        "Total PV Supply (matched)": total_supply,
        "Used Solar": used_solar,
        "Spillage": total_supply - used_solar,
        "Solar Share (%)": (used_solar / total_demand) * 100 if total_demand > 0 else 0.0,
        "Utilisation (%)": (used_solar / total_supply) * 100 if total_supply > 0 else 0.0,
    }])

    merged["Season"] = merged["Date_dt"].dt.month.apply(_season_from_month)
    seasonal_rows = []
    for season in ["DJF", "JJA", "SHOULDER"]:
        sub = merged[merged["Season"] == season]
        d = sub[[f"{c}_demand" for c in hour_cols]].to_numpy().sum()
        s = sub[[f"{c}_supply" for c in hour_cols]].to_numpy().sum()
        u = sub[used_cols].to_numpy().sum()
        seasonal_rows.append({
            "Season": season,
            "Days": len(sub),
            "Total Demand": d,
            "Total PV Supply (matched)": s,
            "Used Solar": u,
            "Spillage": s - u,
            "Solar Share (%)": (u / d) * 100 if d > 0 else 0.0,
            "Utilisation (%)": (u / s) * 100 if s > 0 else 0.0,
        })

    return annual, pd.DataFrame(seasonal_rows), merged


def save_metrics_workbook(demand_df: pd.DataFrame, supply_df: pd.DataFrame) -> bytes:
    """Return the solar metrics Excel workbook as bytes."""
    annual, seasonal, detail = compute_metrics(demand_df, supply_df)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        annual.to_excel(writer, sheet_name="Annual Summary", index=False)
        seasonal.to_excel(writer, sheet_name="Seasonal Summary", index=False)
        detail.to_excel(writer, sheet_name="Matched Detail", index=False)
    return buf.getvalue()


def _df_to_xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Chart generators (returned as PNG bytes alongside existing avg_profile_png)
# ---------------------------------------------------------------------------

def build_seasonal_solar_chart(demand_df: pd.DataFrame, supply_df: pd.DataFrame) -> bytes:
    """Line chart: average hourly Used Solar by season (Winter/Spring/Summer/Autumn)."""
    _, _, merged = compute_metrics(demand_df, supply_df)
    hour_cols = [f"{h:02d}:00" for h in range(24)]

    merged["_season4"] = merged["Date_dt"].dt.month.apply(_season_label)
    season_order = ["Winter", "Spring", "Summer", "Autumn"]
    colours = {
        "Winter": "#1e75bb",
        "Spring": "#e22a87",
        "Summer": "#ffd300",
        "Autumn": "#6b7280",
    }

    x = list(range(24))
    fig, ax = plt.subplots(figsize=(12, 6))
    for season in season_order:
        sub = merged[merged["_season4"] == season]
        if sub.empty:
            continue
        means = [sub[f"{c}_used"].mean() for c in hour_cols]
        ax.plot(x, means, label=season, color=colours[season], linewidth=2.5)

    ax.set_xticks(x)
    ax.set_xticklabels(hour_cols, rotation=45, ha="right")
    ax.set_xlabel("Hour")
    ax.set_ylabel("Energy (same units as input files)")
    ax.set_title("Average Hourly Solar Yield by Season")
    ax.legend()
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def build_daytype_demand_chart(demand_df: pd.DataFrame) -> bytes:
    """Line chart: average hourly traction demand by Weekday / Saturday / Sunday."""
    d_df = _normalize_hour_columns(demand_df.copy())
    hour_cols = _ensure_24_hours(d_df, "DEMAND")
    d_df["Date_dt"] = _parse_date_series(d_df["Date"])
    dow = d_df["Date_dt"].dt.dayofweek  # 0=Mon … 6=Sun
    d_df["_daytype"] = dow.apply(
        lambda d: "Sunday" if d == 6 else ("Saturday" if d == 5 else "Weekday")
    )
    for c in hour_cols:
        d_df[c] = pd.to_numeric(d_df[c], errors="coerce").fillna(0)

    x = list(range(24))
    fig, ax = plt.subplots(figsize=(12, 6))
    colours = {"Weekday": "#e22a87", "Saturday": "#1e75bb", "Sunday": "#ffd300"}
    for label, colour in colours.items():
        sub = d_df[d_df["_daytype"] == label]
        if sub.empty:
            continue
        means = [sub[c].mean() for c in hour_cols]
        ax.plot(x, means, label=label, color=colour, linewidth=2.5)

    ax.set_xticks(x)
    ax.set_xticklabels(hour_cols, rotation=45, ha="right")
    ax.set_xlabel("Hour")
    ax.set_ylabel("Average Demand (same units as input)")
    ax.set_title("Average Hourly Traction Demand by Day Type")
    ax.legend()
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_solar_pipeline(demand_csv: str, pvgis_csv: str) -> SolarResult:
    """Run the full solar analysis pipeline.

    Args:
        demand_csv: CSV text of the half-hour TSS demand file (output from Electric pipeline).
        pvgis_csv:  CSV text of the PVGIS hourly irradiance download.

    Returns:
        SolarResult with all 5 outputs as bytes plus summary metrics.
    """
    # Step 1 — demand HH → hourly
    demand_hourly_df = hh_wide_to_hourly_wide(demand_csv)

    # Step 2 — PVGIS → hourly wide
    pvgis_hourly_df = clean_pvgis_to_wide_hourly(pvgis_csv)

    # Step 3 — average profile + plot
    _, png_bytes, avg_profile_xlsx = build_average_profile(demand_hourly_df, pvgis_hourly_df)

    # Step 4 — solar metrics workbook
    metrics_xlsx = save_metrics_workbook(demand_hourly_df, pvgis_hourly_df)

    # Extract top-level KPIs for API response
    annual, _, _ = compute_metrics(demand_hourly_df, pvgis_hourly_df)
    solar_share = float(annual["Solar Share (%)"].iloc[0])
    utilisation = float(annual["Utilisation (%)"].iloc[0])

    # Step 5 — additional charts
    seasonal_chart_png = build_seasonal_solar_chart(demand_hourly_df, pvgis_hourly_df)
    daytype_chart_png = build_daytype_demand_chart(demand_hourly_df)

    return SolarResult(
        demand_hourly_xlsx=_df_to_xlsx_bytes(demand_hourly_df),
        pvgis_supply_xlsx=_df_to_xlsx_bytes(pvgis_hourly_df),
        avg_profile_xlsx=avg_profile_xlsx,
        avg_profile_png=png_bytes,
        metrics_xlsx=metrics_xlsx,
        solar_share_pct=round(solar_share, 2),
        utilisation_pct=round(utilisation, 2),
        seasonal_chart_png=seasonal_chart_png,
        daytype_chart_png=daytype_chart_png,
    )
