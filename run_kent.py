#!/usr/bin/env python3
"""Batch runner: generate timetable, route, and energy data for all Kent stations.

Usage
-----
1. Start the backend:
       uvicorn backend.app.main:app --reload

2. (First run only) upload the CIF file if not already loaded:
       curl -X POST http://localhost:8000/api/cif/upload \
            -F "file=@backend/data/cif/toc-full.CIF.gz"

3. Edit the CONFIG block below to match your local file paths.

4. Run:
       python run_kent.py

Outputs are written to kent_output/ next to this script:
  kent_output/<station_name>/timetable.csv
  kent_output/<station_name>/route.csv
  kent_output/energy/<tss_name>.csv
  kent_output/run_summary.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# CONFIG — edit these before running
# ---------------------------------------------------------------------------

BASE_URL  = "http://localhost:8000"   # backend URL
DATE_START = "2026-05-01"
DATE_END   = "2026-05-10"

# Paths to reference CSVs that the electric pipeline needs.
# Set to None to skip the electric energy run.
ROLLING_STOCK_PATH  = Path("backend/data/electric/rolling_stock.csv")
STATION_POINTS_PATH = Path("backend/data/electric/station_points.csv")

OUTPUT_DIR = Path("kent_output")

# ---------------------------------------------------------------------------
# Kent stations — (station_name_as_in_CORPUS, operator_code)
# ---------------------------------------------------------------------------

KENT_STATIONS: list[tuple[str, str]] = [
    # North Kent / Thames corridor
    ("Dartford",                  "SE"),
    ("Gravesend",                 "SE"),
    ("Higham",                    "SE"),
    ("Strood",                    "SE"),
    ("Rochester",                 "SE"),
    ("Chatham",                   "SE"),
    ("Gillingham",                "SE"),
    ("Rainham",                   "SE"),
    ("Newington",                 "SE"),
    ("Sittingbourne",             "SE"),
    ("Teynham",                   "SE"),
    ("Faversham",                 "SE"),
    # Medway Valley
    ("Maidstone East",            "SE"),
    ("Maidstone West",            "SE"),
    ("Maidstone Barracks",        "SE"),
    ("Cuxton",                    "SE"),
    ("Halling",                   "SE"),
    ("Snodland",                  "SE"),
    ("New Hythe",                 "SE"),
    ("Aylesford",                 "SE"),
    ("Barming",                   "SE"),
    # Swanley / Sevenoaks branch
    ("Swanley",                   "SE"),
    ("Eynsford",                  "SE"),
    ("Shoreham",                  "SE"),
    ("Otford",                    "SE"),
    ("Bat & Ball",                "SE"),
    ("Sevenoaks",                 "SE"),
    # Ebbsfleet / HS1
    ("Ebbsfleet International",   "SE"),
    # Borough Green / Maidstone West branch
    ("Borough Green & Wrotham",   "SE"),
    ("Kemsing",                   "SE"),
    # Tonbridge / Weald
    ("Tonbridge",                 "SE"),
    ("Tunbridge Wells",           "SE"),
    ("Paddock Wood",              "SE"),
    ("Yalding",                   "SE"),
    ("East Farleigh",             "SE"),
    ("Wateringbury",              "SE"),
    ("Staplehurst",               "SE"),
    ("Marden",                    "SE"),
    ("Headcorn",                  "SE"),
    # Ashford area
    ("Ashford International",     "SE"),
    ("Pluckley",                  "SE"),
    ("Wye",                       "SE"),
    ("Chilham",                   "SE"),
    # Canterbury / Whitstable / Thanet
    ("Canterbury East",           "SE"),
    ("Canterbury West",           "SE"),
    ("Bekesbourne",               "SE"),
    ("Selling",                   "SE"),
    ("Whitstable",                "SE"),
    ("Herne Bay",                 "SE"),
    ("Margate",                   "SE"),
    ("Broadstairs",               "SE"),
    ("Ramsgate",                  "SE"),
    # East Kent
    ("Sandwich",                  "SE"),
    ("Deal",                      "SE"),
    ("Walmer",                    "SE"),
    ("Martin Mill",               "SE"),
    ("Kearsney",                  "SE"),
    ("Dover Priory",              "SE"),
    # Folkestone
    ("Folkestone West",           "SE"),
    ("Folkestone Central",        "SE"),
    # Ham Street / Romney Marsh / Rye
    ("Ham Street",                "SE"),
    ("Appledore",                 "SE"),
    ("Rye",                       "SE"),
    ("Ore",                       "SE"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers["Accept"] = "application/json"


def _url(path: str) -> str:
    return BASE_URL.rstrip("/") + path


def _check_backend() -> None:
    try:
        r = _SESSION.get(_url("/api/health"), timeout=5)
        r.raise_for_status()
    except Exception as exc:
        print(f"\nERROR: Cannot reach backend at {BASE_URL}: {exc}")
        print("Start it with:  uvicorn backend.app.main:app --reload")
        sys.exit(1)


def _poll_job(job_id: str, poll_url: str, label: str,
              interval: float = 2.0, timeout: float = 600.0) -> dict:
    """Poll a job URL until status is 'done' or 'error'."""
    deadline = time.monotonic() + timeout
    dots = 0
    while time.monotonic() < deadline:
        r = _SESSION.get(poll_url, timeout=15)
        r.raise_for_status()
        data = r.json()
        status = data.get("status", "")
        if status == "done":
            print(f"  done")
            return data
        if status == "error":
            raise RuntimeError(f"{label} job {job_id} failed: {data.get('error', '?')}")
        time.sleep(interval)
        dots += 1
        if dots % 10 == 0:
            print(f"  ... still waiting ({dots * interval:.0f}s)", flush=True)
    raise TimeoutError(f"{label} job {job_id} timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Phase 1: upload reference files for the electric pipeline
# ---------------------------------------------------------------------------

def phase_1_upload_references() -> bool:
    """Upload rolling_stock.csv and station_points.csv. Returns True if successful."""
    if ROLLING_STOCK_PATH is None or STATION_POINTS_PATH is None:
        print("[Phase 1] Skipping reference upload (paths not configured).")
        return False

    for label, path, file_type in [
        ("rolling_stock",  ROLLING_STOCK_PATH,  "rolling_stock"),
        ("station_points", STATION_POINTS_PATH, "station_points"),
    ]:
        if not path.exists():
            print(f"[Phase 1] WARNING: {label} file not found at {path} — electric run will be skipped.")
            return False
        print(f"[Phase 1] Uploading {label} ...", end=" ", flush=True)
        with path.open("rb") as fh:
            r = _SESSION.post(
                _url(f"/api/electric/upload/{file_type}"),
                files={"file": (path.name, fh, "text/csv")},
                timeout=30,
            )
        r.raise_for_status()
        print("ok")
    return True


# ---------------------------------------------------------------------------
# Phase 2: generate timetable + route for every station
# ---------------------------------------------------------------------------

def phase_2_generate_stations() -> list[dict]:
    """Run timetable generation for each Kent station. Returns list of result dicts."""
    results = []
    total = len(KENT_STATIONS)

    for idx, (station_name, operator_code) in enumerate(KENT_STATIONS, 1):
        safe = station_name.replace(" ", "_").replace("&", "and").replace("/", "-")
        print(f"[Phase 2] ({idx}/{total}) {station_name} ... ", end="", flush=True)

        # Submit job
        try:
            r = _SESSION.post(
                _url("/api/generate"),
                data={
                    "station_name":  station_name,
                    "operator_code": operator_code,
                    "date_start":    DATE_START,
                    "date_end":      DATE_END,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"SUBMIT ERROR: {exc}")
            results.append({"station": station_name, "status": "error", "error": str(exc)})
            continue

        if r.status_code == 400:
            # Station not found in CORPUS or CIF not loaded — skip gracefully
            print(f"skipped ({r.json().get('detail', r.text)[:80]})")
            results.append({"station": station_name, "status": "skipped",
                            "reason": r.json().get("detail", "")})
            continue

        try:
            r.raise_for_status()
        except requests.HTTPError as exc:
            print(f"HTTP ERROR: {exc}")
            results.append({"station": station_name, "status": "error", "error": str(exc)})
            continue

        job_id = r.json()["job_id"]

        # Poll to completion
        try:
            job_result = _poll_job(
                job_id,
                poll_url=_url(f"/api/generate/status/{job_id}"),
                label=station_name,
            )
        except (RuntimeError, TimeoutError) as exc:
            print(f"POLL ERROR: {exc}")
            results.append({"station": station_name, "status": "error", "error": str(exc)})
            continue

        gen_id    = job_result["generation_id"]
        tt_rows   = job_result.get("timetable_rows", 0)
        rt_rows   = job_result.get("route_rows", 0)
        warnings  = job_result.get("warnings", [])

        if tt_rows == 0:
            print(f"  WARNING: no timetable rows — station may not match CIF data")

        results.append({
            "station":       station_name,
            "status":        "done",
            "generation_id": gen_id,
            "timetable_rows": tt_rows,
            "route_rows":    rt_rows,
            "warnings":      warnings,
        })

    return results


# ---------------------------------------------------------------------------
# Phase 3: run the electric pipeline on all successful generations
# ---------------------------------------------------------------------------

def phase_3_run_electric(gen_ids: list[str]) -> str | None:
    """Submit one combined electric pipeline job. Returns run_id or None."""
    if not gen_ids:
        print("[Phase 3] No successful generations — skipping electric run.")
        return None

    print(f"\n[Phase 3] Running electric pipeline across {len(gen_ids)} station result(s)...", flush=True)
    r = _SESSION.post(
        _url("/api/electric/run"),
        json={"result_ids": gen_ids},
        timeout=30,
    )
    if r.status_code == 400:
        print(f"  Skipped: {r.json().get('detail', r.text)[:120]}")
        return None
    r.raise_for_status()

    job_id = r.json()["job_id"]
    result = _poll_job(
        job_id,
        poll_url=_url(f"/api/electric/job/{job_id}"),
        label="electric",
        interval=3.0,
        timeout=1800.0,
    )
    run_id = result.get("run_id")
    tss_count = len(result.get("tss_outputs", {}))
    print(f"  Electric run_id={run_id}, TSS files produced: {tss_count}")
    return run_id


# ---------------------------------------------------------------------------
# Phase 4: download all output CSVs
# ---------------------------------------------------------------------------

def phase_4_download_outputs(station_results: list[dict], run_id: str | None) -> None:
    """Download timetable/route CSVs per station and TSS energy CSVs."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Per-station timetable + route
    print("\n[Phase 4] Downloading per-station CSVs...")
    for res in station_results:
        if res["status"] != "done":
            continue
        gen_id  = res["generation_id"]
        station = res["station"]
        safe    = station.replace(" ", "_").replace("&", "and").replace("/", "-")
        dest    = OUTPUT_DIR / safe
        dest.mkdir(parents=True, exist_ok=True)

        for csv_type in ("timetable", "route"):
            r = _SESSION.get(_url(f"/api/download/{gen_id}/{csv_type}.csv"), timeout=30)
            if r.status_code == 200:
                (dest / f"{csv_type}.csv").write_bytes(r.content)
            else:
                print(f"  WARNING: could not download {csv_type}.csv for {station}")

        print(f"  {station}: {res['timetable_rows']} timetable rows, {res['route_rows']} route rows")

    # Electric TSS energy CSVs
    if run_id:
        print("\n[Phase 4] Downloading energy CSVs...")
        r = _SESSION.get(_url(f"/api/electric/job/{run_id}"), timeout=15)
        r.raise_for_status()
        job_data = r.json()
        tss_outputs: dict = job_data.get("tss_outputs", {})

        energy_dir = OUTPUT_DIR / "energy"
        energy_dir.mkdir(parents=True, exist_ok=True)

        for tss_name, filename in tss_outputs.items():
            r2 = _SESSION.get(_url(f"/api/electric/output/{run_id}/{filename}"), timeout=30)
            if r2.status_code == 200:
                (energy_dir / filename).write_bytes(r2.content)
                print(f"  {tss_name}: saved {filename}")
            else:
                print(f"  WARNING: could not download energy file for TSS {tss_name}")

        # Debug mismatch CSV
        r3 = _SESSION.get(_url(f"/api/electric/debug/{run_id}"), timeout=15)
        if r3.status_code == 200 and r3.content.strip():
            (OUTPUT_DIR / "energy" / "debug_mismatches.csv").write_bytes(r3.content)
            print("  debug_mismatches.csv saved (check for station name mismatches)")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _write_summary(station_results: list[dict], run_id: str | None) -> None:
    summary = {
        "date_start": DATE_START,
        "date_end":   DATE_END,
        "electric_run_id": run_id,
        "stations": station_results,
        "totals": {
            "submitted": len(station_results),
            "done":      sum(1 for r in station_results if r["status"] == "done"),
            "skipped":   sum(1 for r in station_results if r["status"] == "skipped"),
            "error":     sum(1 for r in station_results if r["status"] == "error"),
            "timetable_rows_total": sum(
                r.get("timetable_rows", 0) for r in station_results if r["status"] == "done"
            ),
        },
    }
    (OUTPUT_DIR / "run_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(f"\nSummary written to {OUTPUT_DIR / 'run_summary.json'}")
    t = summary["totals"]
    print(f"  Done: {t['done']}  Skipped: {t['skipped']}  Errors: {t['error']}")
    print(f"  Total timetable rows across Kent: {t['timetable_rows_total']:,}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Kent Batch Runner ===")
    print(f"Dates:  {DATE_START} → {DATE_END}")
    print(f"Output: {OUTPUT_DIR.resolve()}")
    print(f"Backend:{BASE_URL}\n")

    _check_backend()

    refs_uploaded = phase_1_upload_references()

    station_results = phase_2_generate_stations()

    successful_gen_ids = [
        r["generation_id"]
        for r in station_results
        if r["status"] == "done" and r.get("timetable_rows", 0) > 0
    ]

    run_id: str | None = None
    if refs_uploaded and successful_gen_ids:
        run_id = phase_3_run_electric(successful_gen_ids)

    phase_4_download_outputs(station_results, run_id)

    _write_summary(station_results, run_id)
    print("\nDone.")


if __name__ == "__main__":
    main()
