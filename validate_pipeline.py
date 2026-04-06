#!/usr/bin/env python3
"""
validate_pipeline.py – Comprehensive execution-based validation of the
Electric Train energy pipeline.

Runs the pipeline with production data, tests every parameter sensitivity,
verifies formula correctness with a manual spot-check, and prints
PASS / FAIL / WARN for each assertion.
"""

from __future__ import annotations

import io
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, "/home/user/Electric-Train-Web-App")

import numpy as np
import pandas as pd

from backend.app.services.electric_pipeline import (
    _expand_services,
    _load_energy_params,
    run_pipeline,
    BIN_LABELS,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA = Path("/home/user/Electric-Train-Web-App/backend/data/electric")
TT_PATH   = DATA / "timetable.csv"
RT_PATH   = DATA / "route.csv"
RS_PATH   = DATA / "rolling_stock.csv"
SP_PATH   = DATA / "station_points.csv"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0
_WARN = 0


def _result(label: str, ok: bool, msg: str = "", warn: bool = False) -> None:
    global _PASS, _FAIL, _WARN
    if ok:
        status = "PASS"
        _PASS += 1
    elif warn:
        status = "WARN"
        _WARN += 1
    else:
        status = "FAIL"
        _FAIL += 1
    icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]"}[status]
    detail = f"  → {msg}" if msg else ""
    print(f"  {icon} {label}{detail}")


def _section(title: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}")
    print(f"  {title}")
    print(bar)


def _subsection(title: str) -> None:
    print(f"\n  -- {title} --")


# ---------------------------------------------------------------------------
# Load production data once
# ---------------------------------------------------------------------------
def _load_prod():
    tt = pd.read_csv(TT_PATH)
    rt = pd.read_csv(RT_PATH)
    sp = pd.read_csv(SP_PATH, encoding="utf-8-sig")
    sp.columns = sp.columns.str.strip()
    ep = pd.read_csv(RS_PATH, encoding="utf-8-sig")
    ep.columns = ep.columns.str.strip()
    return tt, rt, sp, ep


def _run_with_params(tt, rt, sp, ep_override: dict) -> pd.DataFrame:
    """Run _expand_services with overridden energy parameters."""
    ep = pd.DataFrame([{
        "train_type": "CAF_URBOS_3",
        "kwh_per_km_per_car": ep_override.get("kwh_per_km_per_car", 0.7),
        "aux_kw_per_car": ep_override.get("aux_kw_per_car", 6.0),
        "drive_eff": ep_override.get("drive_eff", 0.9),
        "regen_eff": ep_override.get("regen_eff", 0.25),
        "line_losses_pct": ep_override.get("line_losses_pct", 0.05),
    }])
    return _expand_services(tt, rt, sp, ep)


def _total_kwh(events: pd.DataFrame) -> float:
    if events.empty or "kwh" not in events.columns:
        return 0.0
    return float(events["kwh"].sum())


def _traction_kwh(events: pd.DataFrame) -> float:
    if events.empty or "kwh" not in events.columns:
        return 0.0
    runs = events[events.get("kind", pd.Series()) == "run"] if "kind" in events.columns else events
    return float(runs["kwh"].sum())


def _aux_kwh(events: pd.DataFrame) -> float:
    if events.empty or "kwh" not in events.columns:
        return 0.0
    dwells = events[events["kind"] == "dwell"] if "kind" in events.columns else pd.DataFrame()
    return float(dwells["kwh"].sum()) if not dwells.empty else 0.0


# ============================================================================
# SECTION A: Pipeline Overview / Baseline
# ============================================================================
_section("A. PIPELINE OVERVIEW — BASELINE RUN (production data)")

tt, rt, sp, ep = _load_prod()

print(f"\n  Production data:")
print(f"    timetable rows  : {len(tt):,}")
print(f"    route segments  : {len(rt):,}")
print(f"    station_points  : {len(sp):,}")
print(f"    rolling stock   : {ep.to_dict('records')}")

baseline_events = _run_with_params(tt, rt, sp, {})
valid = baseline_events[baseline_events.get("missing_tss", False) != True] if "missing_tss" in baseline_events.columns else baseline_events
valid_ev = valid[valid["kwh"].notna()] if "kwh" in valid.columns else pd.DataFrame()

total_kwh_baseline = _total_kwh(valid_ev)
run_ev = valid_ev[valid_ev["kind"] == "run"] if "kind" in valid_ev.columns else valid_ev
dwell_ev = valid_ev[valid_ev["kind"] == "dwell"] if "kind" in valid_ev.columns else pd.DataFrame()

traction_kwh_baseline = float(run_ev["kwh"].sum()) if not run_ev.empty else 0.0
dwell_kwh_baseline = float(dwell_ev["kwh"].sum()) if not dwell_ev.empty else 0.0

n_tss = valid_ev["tss"].nunique() if "tss" in valid_ev.columns else 0
n_events = len(valid_ev)

print(f"\n  Baseline results:")
print(f"    Total kWh       : {total_kwh_baseline:,.1f}")
print(f"    Traction kWh    : {traction_kwh_baseline:,.1f}  ({100*traction_kwh_baseline/total_kwh_baseline:.1f}%)")
print(f"    Dwell aux kWh   : {dwell_kwh_baseline:,.1f}  ({100*dwell_kwh_baseline/total_kwh_baseline:.1f}%)")
print(f"    TSS count       : {n_tss}")
print(f"    Event records   : {n_events:,}")

_result("Baseline produces non-zero total kWh", total_kwh_baseline > 0)
_result("Baseline in expected range (700k–1.2M kWh, post-regen-fix with regen_eff=0.25)",
        700_000 < total_kwh_baseline < 1_200_000,
        f"got {total_kwh_baseline:,.0f}")
_result("At least 9 TSS produced", n_tss >= 9, f"got {n_tss}")
_result("Traction > 0", traction_kwh_baseline > 0)
_result("Dwell aux > 0", dwell_kwh_baseline > 0)


# ============================================================================
# SECTION B: REGEN SENSITIVITY (the critical bug)
# ============================================================================
_section("B. REGEN SENSITIVITY — Bug 1 (Critical)")

_subsection("Without gradient data (production data — bug manifests here)")

regen_vals = [0.0, 0.25, 0.5, 0.9]
regen_totals = {}

for rv in regen_vals:
    ev = _run_with_params(tt, rt, sp, {"regen_eff": rv})
    valid_ev2 = ev[ev["kwh"].notna()] if "kwh" in ev.columns else pd.DataFrame()
    if "missing_tss" in valid_ev2.columns:
        valid_ev2 = valid_ev2[valid_ev2["missing_tss"] != True]
    regen_totals[rv] = _total_kwh(valid_ev2)
    print(f"    regen_eff={rv:.2f} → total kWh = {regen_totals[rv]:,.1f}")

# Bug check: 0.0 and 0.25 should differ if regen works
diff_0_25 = abs(regen_totals[0.0] - regen_totals[0.25])
pct_diff_0_25 = 100 * diff_0_25 / regen_totals[0.0] if regen_totals[0.0] > 0 else 0

diff_0_9 = abs(regen_totals[0.0] - regen_totals[0.9])
all_same = (diff_0_9 < 1.0)

print(f"\n    regen_eff 0.0 vs 0.25 delta : {diff_0_25:,.1f} kWh ({pct_diff_0_25:.2f}%)")
print(f"    regen_eff 0.0 vs 0.9  delta : {diff_0_9:,.1f} kWh")
print(f"    All regen values produce same result: {all_same}")

print(f"\n    [FIX APPLIED] Stop-based regen — fires at every segment regardless of gradient.")
print(f"    regen=0.0 is baseline; higher regen_eff should produce LOWER draw.")

_result(
    "FIXED: regen=0.25 reduces draw vs regen=0.0 (stop-based regen active)",
    regen_totals[0.25] < regen_totals[0.0],
    f"0.0→{regen_totals[0.0]:,.1f}  0.25→{regen_totals[0.25]:,.1f}  delta={diff_0_25:,.1f} kWh ({pct_diff_0_25:.2f}%)",
)
_result(
    "FIXED: regen=0.9 reduces draw vs regen=0.25 (monotone)",
    regen_totals[0.9] < regen_totals[0.25],
    f"0.25→{regen_totals[0.25]:,.1f}  0.9→{regen_totals[0.9]:,.1f}",
)
# regen=0.25 on traction component: saves ~25%*(traction/(traction+aux))
# aux is separate so overall reduction is smaller but should still be >10%
pct_reduction_25 = 100 * (regen_totals[0.0] - regen_totals[0.25]) / regen_totals[0.0] if regen_totals[0.0] > 0 else 0
_result(
    "regen=0.25 saves at least 5% total kWh (credible for tram with aux overhead)",
    pct_reduction_25 >= 5.0,
    f"saves {pct_reduction_25:.1f}%",
)

# Verify production route has no gradient column (fix works without it)
has_gradient = "gradient_percent" in rt.columns
_result("Regen works WITHOUT gradient_percent column in route.csv",
        regen_totals[0.25] < regen_totals[0.0] and not has_gradient,
        f"gradient_percent column {'present' if has_gradient else 'absent'} — regen still works")

_subsection("With synthetic gradient data (gradient bonus adds to stop-based regen)")

# Build synthetic route with gradient_percent (downhill for all segs)
rt_with_grad = rt.copy()
rt_with_grad["gradient_percent"] = -2.0  # all downhill, -2%

regen_totals_grad = {}
for rv in regen_vals:
    ev = _run_with_params(tt, rt_with_grad, sp, {"regen_eff": rv})
    valid_ev3 = ev[ev["kwh"].notna()] if "kwh" in ev.columns else pd.DataFrame()
    if "missing_tss" in valid_ev3.columns:
        valid_ev3 = valid_ev3[valid_ev3["missing_tss"] != True]
    regen_totals_grad[rv] = _total_kwh(valid_ev3)
    print(f"    regen_eff={rv:.2f} (with gradient) → total kWh = {regen_totals_grad[rv]:,.1f}")

diff_grad_0_25 = abs(regen_totals_grad[0.0] - regen_totals_grad[0.25])
pct_grad = 100 * diff_grad_0_25 / regen_totals_grad[0.0] if regen_totals_grad[0.0] > 0 else 0
print(f"\n    regen_eff 0.0 vs 0.25 (with gradient) delta : {diff_grad_0_25:,.1f} kWh ({pct_grad:.2f}%)")

_result(
    "With gradient bonus, regen still reduces draw (0→0.25 measurable difference)",
    diff_grad_0_25 > 100,
    f"delta={diff_grad_0_25:,.1f} kWh ({pct_grad:.2f}%)",
)
_result(
    "Gradient adds extra saving: with-gradient draw ≤ without-gradient draw at regen=0.25",
    regen_totals_grad[0.25] <= regen_totals[0.25] + 1.0,
    f"with_grad={regen_totals_grad[0.25]:,.1f}  without_grad={regen_totals[0.25]:,.1f}",
)


# ============================================================================
# SECTION C: AUX SENSITIVITY
# ============================================================================
_section("C. AUX SENSITIVITY")

aux_vals = [0, 6, 12]
aux_totals = {}
aux_dwell_totals = {}

for av in aux_vals:
    ev = _run_with_params(tt, rt, sp, {"aux_kw_per_car": av})
    valid_ev4 = ev[ev["kwh"].notna()] if "kwh" in ev.columns else pd.DataFrame()
    if "missing_tss" in valid_ev4.columns:
        valid_ev4 = valid_ev4[valid_ev4["missing_tss"] != True]
    total = _total_kwh(valid_ev4)
    dw_ev = valid_ev4[valid_ev4["kind"] == "dwell"] if "kind" in valid_ev4.columns else pd.DataFrame()
    dwell = float(dw_ev["kwh"].sum()) if not dw_ev.empty else 0.0
    aux_totals[av] = total
    aux_dwell_totals[av] = dwell
    print(f"    aux_kw_per_car={av} → total kWh={total:,.1f}  dwell kWh={dwell:,.1f}")

_result("aux=0 gives lower total than aux=6", aux_totals[0] < aux_totals[6])
_result("aux=12 gives higher total than aux=6", aux_totals[12] > aux_totals[6])
_result("aux=0 produces zero dwell kWh", aux_dwell_totals[0] == 0.0, f"got {aux_dwell_totals[0]:.1f}")

# Check ~linear scaling of dwell component
ratio_dwell = aux_dwell_totals[12] / aux_dwell_totals[6] if aux_dwell_totals[6] > 0 else 0
_result(
    "Dwell kWh doubles when aux doubles (6→12)",
    abs(ratio_dwell - 2.0) < 0.01,
    f"ratio={ratio_dwell:.4f}",
)


# ============================================================================
# SECTION D: KWH_PER_KM_PER_CAR SENSITIVITY
# ============================================================================
_section("D. KWH_PER_KM_PER_CAR SENSITIVITY")

kwh_vals = [0, 0.7, 1.4]
kwh_totals = {}

for kv in kwh_vals:
    ev = _run_with_params(tt, rt, sp, {"kwh_per_km_per_car": kv})
    valid_ev5 = ev[ev["kwh"].notna()] if "kwh" in ev.columns else pd.DataFrame()
    if "missing_tss" in valid_ev5.columns:
        valid_ev5 = valid_ev5[valid_ev5["missing_tss"] != True]
    kwh_totals[kv] = _total_kwh(valid_ev5)
    print(f"    kwh_per_km_per_car={kv} → total kWh={kwh_totals[kv]:,.1f}")

_result("kwh=0 gives lower total than kwh=0.7", kwh_totals[0] < kwh_totals[0.7])
_result("kwh=1.4 gives higher total than kwh=0.7", kwh_totals[1.4] > kwh_totals[0.7])

# kwh=0 should still have aux energy
_result("kwh=0 still has positive kWh (from aux)", kwh_totals[0] > 0)


# ============================================================================
# SECTION E: DRIVE_EFF SENSITIVITY
# ============================================================================
_section("E. DRIVE_EFF SENSITIVITY")

drive_vals = [0.5, 0.9, 1.0]
drive_totals = {}

for dv in drive_vals:
    ev = _run_with_params(tt, rt, sp, {"drive_eff": dv})
    valid_ev6 = ev[ev["kwh"].notna()] if "kwh" in ev.columns else pd.DataFrame()
    if "missing_tss" in valid_ev6.columns:
        valid_ev6 = valid_ev6[valid_ev6["missing_tss"] != True]
    drive_totals[dv] = _total_kwh(valid_ev6)
    print(f"    drive_eff={dv} → total kWh={drive_totals[dv]:,.1f}")

_result("drive_eff=0.5 gives higher total than drive_eff=0.9", drive_totals[0.5] > drive_totals[0.9],
        f"0.5→{drive_totals[0.5]:,.0f}  0.9→{drive_totals[0.9]:,.0f}")
_result("drive_eff=1.0 gives lower total than drive_eff=0.9", drive_totals[1.0] < drive_totals[0.9],
        f"1.0→{drive_totals[1.0]:,.0f}  0.9→{drive_totals[0.9]:,.0f}")


# ============================================================================
# SECTION F: LINE_LOSSES_PCT SENSITIVITY
# ============================================================================
_section("F. LINE_LOSSES_PCT SENSITIVITY")

losses_vals = [0.0, 0.05, 0.10]
losses_totals = {}

for lv in losses_vals:
    ev = _run_with_params(tt, rt, sp, {"line_losses_pct": lv})
    valid_ev7 = ev[ev["kwh"].notna()] if "kwh" in ev.columns else pd.DataFrame()
    if "missing_tss" in valid_ev7.columns:
        valid_ev7 = valid_ev7[valid_ev7["missing_tss"] != True]
    losses_totals[lv] = _total_kwh(valid_ev7)
    print(f"    line_losses_pct={lv} → total kWh={losses_totals[lv]:,.1f}")

_result("line_losses=0 gives lower total than 0.05", losses_totals[0.0] < losses_totals[0.05])
_result("line_losses=0.10 gives higher total than 0.05", losses_totals[0.10] > losses_totals[0.05])

ratio_losses = (losses_totals[0.10] - losses_totals[0.0]) / losses_totals[0.0]
_result(
    "10% losses give ~10% more than 0% losses",
    abs(ratio_losses - 0.10) < 0.005,
    f"ratio={ratio_losses:.4f}",
)


# ============================================================================
# SECTION G: DWELL ENERGY SEPARATELY
# ============================================================================
_section("G. DWELL ENERGY — ISOLATION TEST")

# Run with very long dwell time: inject synthetic route with 60-min dwell
rt_long_dwell = rt.copy()
rt_long_dwell["wait_min"] = 60.0

ev_short = _run_with_params(tt, rt, sp, {})
ev_long = _run_with_params(tt, rt_long_dwell, sp, {})

def _sum_kind(ev, kind):
    if ev.empty or "kind" not in ev.columns or "kwh" not in ev.columns:
        return 0.0
    sub = ev[ev["kind"] == kind]
    if "missing_tss" in sub.columns:
        sub = sub[sub["missing_tss"] != True]
    sub = sub[sub["kwh"].notna()]
    return float(sub["kwh"].sum())

dwell_short = _sum_kind(ev_short, "dwell")
dwell_long = _sum_kind(ev_long, "dwell")
run_short = _sum_kind(ev_short, "run")
run_long = _sum_kind(ev_long, "run")

print(f"    Production dwell (0.5 min avg) : {dwell_short:,.1f} kWh")
print(f"    Long dwell (60 min)            : {dwell_long:,.1f} kWh")
print(f"    Run energy (should be same)    : run_short={run_short:,.1f}  run_long={run_long:,.1f}")

_result("Long dwell produces much more dwell energy", dwell_long > dwell_short * 10,
        f"{dwell_long:,.0f} vs {dwell_short:,.0f}")
_result("Run energy unchanged by dwell change", abs(run_short - run_long) < 1.0,
        f"delta={abs(run_short-run_long):.2f}")


# ============================================================================
# SECTION H: MANUAL SPOT-CHECK — FORMULA VERIFICATION
# ============================================================================
_section("H. MANUAL SPOT-CHECK — Formula Correctness")

# Pick first valid segment from first matching route variant
first_seg_rv = rt.iloc[0]["route_variant"]
seg = rt[rt["route_variant"] == first_seg_rv].sort_values("seq").iloc[0]

dist_mi = float(seg["distance"])
run_min = float(seg["run_min"])
wait_min = float(seg.get("wait_min", 0.0))
dist_km = dist_mi * 1.609344

kwh_per_km_per_car = 0.7
aux_kw_per_car = 6.0
drive_eff = 0.9
regen_eff = 0.25
line_losses_pct = 0.05
cars = 5

# Current formula (stop-based regen, as in fixed code):
# regen applied at every segment: gross × (1 - regen_frac) / drive_eff
run_kwh_gross   = dist_km * kwh_per_km_per_car * cars
regen_frac      = max(0.0, min(0.9, regen_eff))
run_kwh_traction = run_kwh_gross * (1.0 - regen_frac) / max(drive_eff, 1e-6)
aux_kwh_run     = aux_kw_per_car * cars * (run_min / 60.0)
aux_kwh_dwell   = aux_kw_per_car * cars * (wait_min / 60.0)
run_kwh_net     = (run_kwh_traction + aux_kwh_run) * (1.0 + line_losses_pct)
dwell_kwh_net   = aux_kwh_dwell * (1.0 + line_losses_pct)

print(f"\n  Segment: {seg['from_station']} → {seg['to_station']}")
print(f"    dist_mi={dist_mi}, dist_km={dist_km:.4f}, run_min={run_min}, wait_min={wait_min}")
print(f"    cars={cars}, kwh_per_km_per_car={kwh_per_km_per_car}, regen_eff={regen_eff}")
print(f"")
print(f"    [Step 1] run_kwh_gross    = {dist_km:.4f} × {kwh_per_km_per_car} × {cars} = {run_kwh_gross:.4f}")
print(f"    [Step 2] run_kwh_traction = {run_kwh_gross:.4f} × (1-{regen_frac}) / {drive_eff} = {run_kwh_traction:.4f}  (stop-based regen + ÷drive_eff)")
print(f"    [Step 3] aux_kwh_run      = {aux_kw_per_car} × {cars} × ({run_min}/60) = {aux_kwh_run:.4f}")
print(f"    [Step 4] aux_kwh_dwell    = {aux_kw_per_car} × {cars} × ({wait_min}/60) = {aux_kwh_dwell:.4f}")
print(f"    [Step 5] run_kwh_net      = ({run_kwh_traction:.4f} + {aux_kwh_run:.4f}) × 1.05 = {run_kwh_net:.4f}")
print(f"    [Step 6] dwell_kwh_net    = {aux_kwh_dwell:.4f} × 1.05 = {dwell_kwh_net:.4f}")

# Verify by running pipeline on just this one service
# Find a service that uses this route variant
tt_sub = tt[tt["route_variant"] == first_seg_rv].head(1).copy()
rt_sub = rt[rt["route_variant"] == first_seg_rv].sort_values("seq").head(1).copy()

# Force only the first segment
ev_single = _run_with_params(tt_sub, rt_sub, sp, {
    "kwh_per_km_per_car": kwh_per_km_per_car,
    "aux_kw_per_car": aux_kw_per_car,
    "drive_eff": drive_eff,
    "regen_eff": regen_eff,
    "line_losses_pct": line_losses_pct,
})

if not ev_single.empty and "kwh" in ev_single.columns:
    valid_single = ev_single[ev_single["kwh"].notna()]
    if "missing_tss" in valid_single.columns:
        valid_single = valid_single[valid_single["missing_tss"] != True]
    if not valid_single.empty:
        pipeline_run_kwh = float(valid_single[valid_single["kind"] == "run"]["kwh"].sum()) if "kind" in valid_single.columns else 0.0
        pipeline_dwell_kwh = float(valid_single[valid_single["kind"] == "dwell"]["kwh"].sum()) if "kind" in valid_single.columns else 0.0
        print(f"\n  Pipeline (1 service, 1 segment):")
        print(f"    run kWh  = {pipeline_run_kwh:.4f}  (manual={run_kwh_net:.4f})")
        print(f"    dwell kWh= {pipeline_dwell_kwh:.4f}  (manual={dwell_kwh_net:.4f})")

        _result(
            "Run kWh spot-check: pipeline matches manual calc",
            abs(pipeline_run_kwh - run_kwh_net) < 0.001,
            f"pipeline={pipeline_run_kwh:.6f}, manual={run_kwh_net:.6f}",
        )
        _result(
            "Dwell kWh spot-check: pipeline matches manual calc",
            abs(pipeline_dwell_kwh - dwell_kwh_net) < 0.001,
            f"pipeline={pipeline_dwell_kwh:.6f}, manual={dwell_kwh_net:.6f}",
        )
    else:
        print("  [WARN] Single-segment test produced no valid events (station not in station_points)")
        _result("Spot-check: got events", False, "no valid events for first segment", warn=True)
else:
    print("  [WARN] Single-segment events empty")
    _result("Spot-check: got events", False, "empty events", warn=True)


# ============================================================================
# SECTION I: FULL run_pipeline() END-TO-END
# ============================================================================
_section("I. FULL run_pipeline() END-TO-END")

with open(TT_PATH) as f:
    tt_text = f.read()
with open(RT_PATH) as f:
    rt_text = f.read()

tss_outputs, debug_csv = run_pipeline(tt_text, rt_text, RS_PATH, SP_PATH)

print(f"\n  TSS outputs returned : {len(tss_outputs)}")
print(f"  Debug CSV            : {'present' if debug_csv else 'None'}")

_result("run_pipeline returns dict", isinstance(tss_outputs, dict))
_result("At least 9 TSS in output", len(tss_outputs) >= 9, f"got {len(tss_outputs)}")

total_from_csvs = 0.0
for name, csv_text in tss_outputs.items():
    df = pd.read_csv(io.StringIO(csv_text))
    total_from_csvs += df["Total Units"].sum()
    bin_cols = [c for c in df.columns if c in BIN_LABELS]
    assert len(bin_cols) == 48, f"{name}: expected 48 bins"

print(f"\n  Total kWh across all TSS CSVs: {total_from_csvs:,.1f}")
# Note: total_kwh_baseline was computed via _expand_services (direct events sum).
# total_from_csvs comes from _aggregate_half_hour which bins events into 30-min slots.
# Cross-TSS segments are counted in both TSS, so TSS-sum == _expand_services sum
# only if all segments are intra-TSS. A small residual from rounding is expected.
_result("Grand total kWh from CSVs within 2% of _expand_services total",
        abs(total_from_csvs - total_kwh_baseline) / max(total_kwh_baseline, 1) < 0.02,
        f"csvs={total_from_csvs:,.1f} vs expand={total_kwh_baseline:,.1f}  diff={abs(total_from_csvs-total_kwh_baseline):,.0f}")


# ============================================================================
# SECTION J: BUG SUMMARY
# ============================================================================
_section("J. BUG SUMMARY")

print("""
  BUG 1 (CRITICAL) — FIXED: regen_eff was a silent no-op
  ────────────────────────────────────────────────────────
  Was      : regen fired ONLY when route CSV had 'gradient_percent' column
             → regen_eff=0.0 and regen_eff=0.9 produced IDENTICAL output
  Fix      : Stop-based regen now fires at every segment (urban tram model).
             regen_frac = regen_eff, traction = gross*(1-regen_frac)/drive_eff
             gradient_percent, when present, adds an optional bonus on downhill segs.
  Evidence : delta(regen 0→0.9) now significant on production data (see Section B).

  BUG 2 (HIGH): kwh_per_km_per_car semantic ambiguity — documented, not patched
  ──────────────────────────────────────────────────────────────────────────────
  Location : electric_pipeline.py line 254-255
  Issue    : Formula divides by drive_eff treating kwh_per_km_per_car as MECHANICAL
             energy. But 0.7 kWh/km/car for CAF Urbos 3 is the published ELECTRICAL
             (pantograph) consumption → inflates traction by 1/0.9 = 11.1%.
  Status   : Unpatched. Decision required: either adjust the CSV value or change
             the formula. Documented in rolling_stock.csv header comment.
""")


# ============================================================================
# FINAL SUMMARY
# ============================================================================
bar = "=" * 70
print(f"\n{bar}")
print(f"  VALIDATION SUMMARY")
print(f"{bar}")
print(f"  PASS : {_PASS}")
print(f"  WARN : {_WARN}")
print(f"  FAIL : {_FAIL}")
print(f"  TOTAL: {_PASS + _WARN + _FAIL}")
print(f"{bar}")
if _FAIL == 0:
    print("  All critical checks passed (bugs are documented as expected behavior).")
else:
    print(f"  {_FAIL} check(s) FAILED — review output above.")
print()
