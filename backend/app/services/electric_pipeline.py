"""Electric Train energy pipeline service.

Adapted from run_pipeline_tss_assigned.py (Aditya-Dev598/Electric-Train) with fixes:

  Bug 1 (High):   Original reads seg.get("dwell_time", 0.0) but our route CSV uses
                  "wait_min" → dwell energy was always 0. Fixed: seg.get("wait_min", 0.0)

  Bug 2 (High):   drive_eff and per-train regen_eff are loaded from rolling_stock CSV
                  but never applied to the energy calculation. Fixed: apply drive_eff to
                  traction kWh and read regen_eff per train-type from the energy params.

  Bug 3 (Medium): tss_points.csv accepted as CLI arg but never read/used anywhere.
                  Removed from required inputs; upload kept for future validation.

  Bug 4 (Low):    BIN_LABELS last entry "24:00:00" inconsistent with "H:MM" format
                  (e.g. "0:30", "1:00" … "23:30"). Standardised to "0:00" (midnight wrap).

Format transform applied internally (scraper → Electric script column names):
  departure_time HH:MM:SS → dep_time HH:MM
  date YYYY-MM-DD         → date DD/MM/YYYY
  distance_miles          → distance (rename)
  number_of_coaches       → cars (rename)
  train_class             → train_type (NOT auto-mapped; user fills via editor)
  stop_type               → dropped
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Half-hour bin labels (Bug 4 fix: consistent H:MM format, last bin "0:00")
# ---------------------------------------------------------------------------

def _build_bins() -> list[str]:
    labels = []
    t = timedelta(minutes=30)
    cur = timedelta(minutes=0)
    for _ in range(48):
        cur += t
        hh = int(cur.total_seconds() // 3600) % 24  # Bug 4: wrap 24 → 0
        mm = int((cur.total_seconds() % 3600) // 60)
        labels.append(f"{hh}:{mm:02d}")
    return labels


BIN_LABELS = _build_bins()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", str(s).strip())
    s = re.sub(r"_+", "_", s)
    return s.strip("_") or "UNKNOWN"


def _parse_dt(date_str: str, time_str: str) -> datetime:
    """Parse date DD/MM/YYYY and time HH:MM into a datetime."""
    d = datetime.strptime(date_str.strip(), "%d/%m/%Y").date()
    t = datetime.strptime(time_str.strip(), "%H:%M").time()
    return datetime.combine(d, t)


def _make_day_bin_edges(day: datetime) -> list[datetime]:
    d0 = datetime(day.year, day.month, day.day, 0, 0, 0)
    return [d0 + timedelta(minutes=30 * i) for i in range(49)]


def _allocate_kwh(start: datetime, end: datetime, kwh: float, edges: list[datetime]) -> np.ndarray:
    out = np.zeros(48, dtype=float)
    if kwh == 0 or end <= start:
        return out
    rate = kwh / (end - start).total_seconds()
    for i in range(48):
        a0 = max(start, edges[i])
        a1 = min(end, edges[i + 1])
        if a1 > a0:
            out[i] += rate * (a1 - a0).total_seconds()
    return out


# ---------------------------------------------------------------------------
# Format transform: scraper output → Electric script columns
# ---------------------------------------------------------------------------

def _transform_timetable(df: pd.DataFrame) -> pd.DataFrame:
    """Convert scraper-output column names/formats to what the pipeline expects."""
    df = df.copy()

    # departure_time HH:MM:SS → dep_time HH:MM
    if "departure_time" in df.columns and "dep_time" not in df.columns:
        df["dep_time"] = df["departure_time"].astype(str).str[:5]

    # date YYYY-MM-DD → DD/MM/YYYY
    if "date" in df.columns:
        def _reformat_date(v: str) -> str:
            v = str(v).strip()
            try:
                return datetime.strptime(v, "%Y-%m-%d").strftime("%d/%m/%Y")
            except ValueError:
                return v  # already in target format or unknown
        df["date"] = df["date"].map(_reformat_date)

    # distance_miles → distance
    if "distance_miles" in df.columns and "distance" not in df.columns:
        df = df.rename(columns={"distance_miles": "distance"})

    # number_of_coaches → cars
    if "number_of_coaches" in df.columns and "cars" not in df.columns:
        df = df.rename(columns={"number_of_coaches": "cars"})

    # train_class → train_type (kept as-is; user fills correct value in editor)
    if "train_class" in df.columns and "train_type" not in df.columns:
        df = df.rename(columns={"train_class": "train_type"})

    # drop stop_type if present
    df = df.drop(columns=[c for c in ["stop_type"] if c in df.columns])

    return df


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def _load_energy_params(path: Path, train_types: list[str]) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path, encoding="utf-8-sig")
        df.columns = df.columns.str.strip()
        required = {"train_type", "kwh_per_km_per_car", "aux_kw_per_car"}
        missing = sorted(required - set(df.columns))
        if missing:
            raise ValueError(f"rolling_stock CSV missing columns: {missing}")
        return df
    # Default placeholders
    rows = []
    for tt in sorted(set(train_types)):
        rows.append({
            "train_type": tt,
            "kwh_per_km_per_car": 2.0,
            "aux_kw_per_car": 10.0,
            "drive_eff": 0.9,
            "regen_eff": 0.0,
            "line_losses_pct": 0.0,
        })
    return pd.DataFrame(rows)


def _expand_services(
    tt: pd.DataFrame,
    route: pd.DataFrame,
    stations: pd.DataFrame,
    energy_params: pd.DataFrame,
    split_cross_tss: float = 0.5,
) -> pd.DataFrame:
    st = stations.copy()
    st["Station_norm"] = st["Station"].astype(str).str.strip().str.upper()
    tss_map = dict(zip(st["Station_norm"], st["TSS"].astype(str).str.strip()))

    ep = energy_params.set_index("train_type")
    records = []

    for idx, row in tt.iterrows():
        rv = str(row["route_variant"]).strip()
        date = str(row["date"]).strip()
        dep_time = str(row["dep_time"]).strip()
        train_type = str(row["train_type"]).strip()
        try:
            cars = int(row["cars"])
        except (ValueError, TypeError):
            cars = 1

        try:
            start_dt = _parse_dt(date, dep_time)
        except ValueError:
            continue

        segs = route[route["route_variant"].astype(str).str.strip() == rv].sort_values("seq")
        if segs.empty:
            continue

        # Lookup energy params for this train type
        params = ep.loc[train_type] if train_type in ep.index else None
        kwh_per_km_per_car = float(params["kwh_per_km_per_car"]) if params is not None else 2.0
        aux_kw_per_car = float(params["aux_kw_per_car"]) if params is not None else 10.0
        line_losses_pct = float(params.get("line_losses_pct", 0.0)) if params is not None else 0.0
        # Bug 2 fix: read drive_eff and regen_eff per train type
        drive_eff = float(params.get("drive_eff", 0.9)) if params is not None else 0.9
        regen_eff = float(params.get("regen_eff", 0.0)) if params is not None else 0.0

        cur_dt = start_dt

        for _, seg in segs.iterrows():
            from_st = str(seg["from_station"]).strip().upper()
            to_st = str(seg["to_station"]).strip().upper()

            dist_raw = seg.get("distance", "")
            run_raw  = seg.get("run_min", "")
            try:
                _dist_mi = float(dist_raw)
                if _dist_mi != _dist_mi:   # NaN check
                    raise ValueError
            except (TypeError, ValueError):
                _dist_mi = None
            try:
                _run_min = float(run_raw)
                if _run_min != _run_min:   # NaN check
                    raise ValueError
            except (TypeError, ValueError):
                _run_min = None

            if _dist_mi is None or _run_min is None:
                logger.warning(
                    "Skipping segment %s→%s in variant '%s': blank/invalid distance or run_min",
                    from_st, to_st, rv,
                )
                records.append({
                    "service_row": idx, "route_variant": rv,
                    "from_station": from_st, "to_station": to_st,
                    "missing_tss": True, "from_tss": None, "to_tss": None,
                })
                cur_dt += timedelta(minutes=(_run_min or 0))
                continue

            dist_km = _dist_mi * 1.609344
            run_min = _run_min
            # Bug 1 fix: use wait_min instead of dwell_time
            dwell_min = float(seg.get("wait_min", 0.0) or 0.0)

            from_tss = tss_map.get(from_st)
            to_tss = tss_map.get(to_st)

            if from_tss is None or to_tss is None:
                records.append({
                    "service_row": idx, "route_variant": rv,
                    "from_station": from_st, "to_station": to_st,
                    "missing_tss": True, "from_tss": from_tss, "to_tss": to_tss,
                })
                cur_dt += timedelta(minutes=run_min + dwell_min)
                continue

            # Bug 2 fix: apply drive_eff to traction energy
            run_kwh_gross = dist_km * kwh_per_km_per_car * cars
            run_kwh_traction = run_kwh_gross / max(drive_eff, 1e-6)  # wall-socket kWh

            # Regen on downhill gradient (Bug 2: now uses per-train regen_eff)
            if "gradient_percent" in seg.index:
                g = pd.to_numeric(seg["gradient_percent"], errors="coerce")
                if pd.notna(g) and g < 0:
                    run_kwh_traction *= 1.0 - max(0.0, min(0.9, regen_eff))

            aux_kwh_run = aux_kw_per_car * cars * (run_min / 60.0)
            aux_kwh_dwell = aux_kw_per_car * cars * (dwell_min / 60.0)

            run_kwh_net = (run_kwh_traction + aux_kwh_run) * (1.0 + line_losses_pct)

            run_start = cur_dt
            run_end = cur_dt + timedelta(minutes=run_min)
            dwell_start = run_end
            dwell_end = run_end + timedelta(minutes=dwell_min)
            cur_dt = dwell_end

            if from_tss == to_tss:
                records.append({
                    "date": date, "route_variant": rv, "train_type": train_type, "cars": cars,
                    "tss": from_tss, "kind": "run", "start": run_start, "end": run_end, "kwh": run_kwh_net,
                })
            else:
                a = max(0.0, min(1.0, split_cross_tss))
                records.append({
                    "date": date, "route_variant": rv, "train_type": train_type, "cars": cars,
                    "tss": from_tss, "kind": "run", "start": run_start, "end": run_end, "kwh": run_kwh_net * a,
                })
                records.append({
                    "date": date, "route_variant": rv, "train_type": train_type, "cars": cars,
                    "tss": to_tss, "kind": "run", "start": run_start, "end": run_end, "kwh": run_kwh_net * (1.0 - a),
                })

            if aux_kwh_dwell > 0:
                dwell_kwh_net = aux_kwh_dwell * (1.0 + line_losses_pct)
                records.append({
                    "date": date, "route_variant": rv, "train_type": train_type, "cars": cars,
                    "tss": to_tss, "kind": "dwell", "start": dwell_start, "end": dwell_end, "kwh": dwell_kwh_net,
                })

    return pd.DataFrame(records) if records else pd.DataFrame()


def _aggregate_half_hour(events: pd.DataFrame) -> dict[str, str]:
    """Return dict of TSS name → CSV string (48 half-hour bin columns per day)."""
    if events.empty:
        return {}

    ev = events.copy()
    if "missing_tss" in ev.columns:
        ev = ev[ev["missing_tss"] != True]  # noqa: E712
    if ev.empty:
        return {}

    ev["date_dt"] = pd.to_datetime(ev["date"], dayfirst=True, errors="coerce")
    out: dict[str, str] = {}

    for tss, group in ev.groupby("tss"):
        rows = []
        for day, gday in group.groupby(group["date_dt"].dt.date):
            day_dt = datetime(day.year, day.month, day.day)
            edges = _make_day_bin_edges(day_dt)
            bins = np.zeros(48, dtype=float)
            for _, r in gday.iterrows():
                bins += _allocate_kwh(r["start"], r["end"], float(r["kwh"]), edges)
            row: dict = {
                "Date": day_dt.strftime("%d/%m/%Y"),
                "Date_dt": day_dt,
                "Day": day_dt.strftime("%A"),
                "Total Units": bins.sum(),
            }
            for label, val in zip(BIN_LABELS, bins):
                row[label] = val
            rows.append(row)

        df = (
            pd.DataFrame(rows)
            .sort_values("Date_dt")
            .drop(columns=["Date_dt"])
        )
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        out[_safe_name(str(tss))] = buf.getvalue()

    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _build_debug_csv(events: pd.DataFrame, route: pd.DataFrame, stations: pd.DataFrame) -> str | None:
    """Build a debug CSV of station mismatches. Returns None if no mismatches exist."""
    if events.empty or "missing_tss" not in events.columns:
        return None

    mismatches = events[events["missing_tss"] == True].copy()  # noqa: E712
    if mismatches.empty:
        return None

    # Build the known station set (upper-cased, as used in lookup)
    known = set(stations["Station"].astype(str).str.strip().str.upper().tolist())

    rows = []
    seen = set()
    for _, r in mismatches.iterrows():
        rv = r.get("route_variant", "")
        fs = r.get("from_station", "")
        ts = r.get("to_station", "")
        key = (rv, fs, ts)
        if key in seen:
            continue
        seen.add(key)

        missing_side = []
        if fs and fs.upper() not in known:
            missing_side.append("from_station")
        if ts and ts.upper() not in known:
            missing_side.append("to_station")

        # Suggest close matches from station_points
        def _suggest(name: str) -> str:
            if not name:
                return ""
            name_u = name.upper()
            return "; ".join(
                s for s in sorted(known)
                if name_u[:3] in s or s[:3] in name_u
            )[:120] or "—"

        rows.append({
            "route_variant": rv,
            "from_station": fs,
            "to_station": ts,
            "missing_side": " & ".join(missing_side) or "unknown",
            "from_tss_resolved": str(r.get("from_tss", "") or ""),
            "to_tss_resolved": str(r.get("to_tss", "") or ""),
            "suggestion_from": _suggest(fs) if "from_station" in missing_side else "ok",
            "suggestion_to": _suggest(ts) if "to_station" in missing_side else "ok",
        })

    if not rows:
        return None

    buf = io.StringIO()
    fieldnames = ["route_variant", "from_station", "to_station", "missing_side",
                  "from_tss_resolved", "to_tss_resolved", "suggestion_from", "suggestion_to"]
    import csv as _csv
    writer = _csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def run_pipeline(
    timetable_csv: str,
    route_csv: str,
    rolling_stock_path: Path,
    station_points_path: Path,
    split_cross_tss: float = 0.5,
) -> tuple[dict[str, str], str | None]:
    """Run the Electric Train energy pipeline.

    Args:
        timetable_csv: CSV text from the scraper (scraper column names accepted).
        route_csv: CSV text from the scraper.
        rolling_stock_path: Path to rolling_stock_energy.csv.
        station_points_path: Path to station_points.csv (Station → TSS mapping).
        split_cross_tss: Fraction of cross-TSS segment energy assigned to the from-TSS.

    Returns:
        Tuple of:
        - Dict of TSS name (safe filename stem) → CSV string with 48 half-hour kWh bins.
          Empty dict if no output was produced.
        - Debug CSV string listing station mismatches, or None if all stations resolved.
    """
    tt = pd.read_csv(io.StringIO(timetable_csv.lstrip("\ufeff")))
    tt.columns = tt.columns.str.strip()
    route = pd.read_csv(io.StringIO(route_csv.lstrip("\ufeff")))
    route.columns = route.columns.str.strip()
    stations = pd.read_csv(station_points_path, encoding="utf-8-sig")
    stations.columns = stations.columns.str.strip()

    # Apply format transform (handles scraper column names transparently)
    tt = _transform_timetable(tt)

    # Validate post-transform
    for col in ["route_variant", "date", "dep_time", "train_type", "cars"]:
        if col not in tt.columns:
            raise ValueError(f"Timetable CSV missing required column '{col}' (after transform)")
    if "distance_miles" in route.columns and "distance" not in route.columns:
        route = route.rename(columns={"distance_miles": "distance"})
    for col in ["route_variant", "seq", "from_station", "to_station", "distance", "run_min"]:
        if col not in route.columns:
            raise ValueError(f"Route CSV missing required column '{col}'")
    for col in ["Station", "TSS"]:
        if col not in stations.columns:
            raise ValueError(f"station_points CSV missing required column '{col}'")

    energy_params = _load_energy_params(rolling_stock_path, tt["train_type"].astype(str).tolist())
    events = _expand_services(tt, route, stations, energy_params, split_cross_tss)
    tss_outputs = _aggregate_half_hour(events)
    debug_csv = _build_debug_csv(events, route, stations)

    return tss_outputs, debug_csv


def combine_csvs(csv_texts: list[str]) -> str:
    """Combine multiple timetable CSV texts with service-level deduplication.

    Dedup key: (date, train_uid, origin_departure) when both columns are present.
    - train_uid identifies the CIF schedule.
    - origin_departure (LO record scheduled_departure) disambiguates STP P+N
      services that share a UID but depart their origin at different times.

    This ensures the same physical service appearing in multiple station outputs
    (e.g. Fleet + Camberley) is collapsed to one row, while genuinely distinct
    services — including STP New workings alongside a Permanent with the same UID
    — are correctly preserved.

    Among duplicates the row with the most non-empty fields is kept; ties are
    broken by earliest departure_time (pre-sorted ascending) so the station
    closest to the service origin is preferred.

    Fallback: (date, departure_time, route_variant) for legacy CSVs that lack
    the train_uid / origin_departure columns.
    """
    frames = [
        pd.read_csv(io.StringIO(t.lstrip("\ufeff")))
        for t in csv_texts if t.strip()
    ]
    if not frames:
        return ""
    combined = pd.concat(frames, ignore_index=True)

    service_key = ["date", "train_uid", "origin_departure"]
    legacy_key = ["date", "departure_time", "route_variant"]

    if (
        all(c in combined.columns for c in service_key)
        and combined["train_uid"].notna().any()
    ):
        key_cols = service_key
        # Pre-sort by departure_time ascending: on a score tie the row from the
        # station closest to the service origin (earliest dep_time) is kept.
        combined = combined.sort_values("departure_time", ascending=True)
    else:
        key_cols = legacy_key

    if all(c in combined.columns for c in key_cols):
        # Score each row by number of non-empty / non-null fields (higher = more data)
        combined["_score"] = combined.apply(
            lambda r: sum(1 for v in r if v is not None and str(v).strip() not in ("", "nan")),
            axis=1,
        )
        combined = (
            combined
            .sort_values("_score", ascending=False)
            .drop_duplicates(subset=key_cols, keep="first")
            .drop(columns=["_score"])
        )
    else:
        combined = combined.drop_duplicates()

    buf = io.StringIO()
    combined.to_csv(buf, index=False)
    return buf.getvalue()


def combine_route_csvs(csv_texts: list[str]) -> str:
    """Combine multiple route CSV texts using whole-route fingerprinting.

    Two route_variant groups are considered identical only if every segment
    (seq, from_station, to_station) matches exactly.  Same-named routes with
    different stop sequences are kept as distinct entries — only fully
    identical routes are deduplicated.
    """
    seen_fingerprints: set[tuple] = set()
    kept: list[pd.DataFrame] = []
    for text in csv_texts:
        if not text.strip():
            continue
        frame = pd.read_csv(io.StringIO(text.lstrip("\ufeff")))
        frame.columns = frame.columns.str.strip()
        for _rv, group in frame.groupby("route_variant"):
            g = group.sort_values("seq")
            fp = tuple(zip(
                g["seq"].tolist(),
                g["from_station"].tolist(),
                g["to_station"].tolist(),
            ))
            if fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                kept.append(g)
    if not kept:
        return ""
    buf = io.StringIO()
    pd.concat(kept, ignore_index=True).to_csv(buf, index=False)
    return buf.getvalue()
