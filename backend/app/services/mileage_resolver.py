"""Mileage resolver: lookup official rail distances between stations.

Primary source: Network Rail NESA / Rail Data Marketplace official mileage
datasets (backend/data/mileage/mileage.json).

Fallback (when mileage.json absent or a pair is not found): coordinate-based
estimation using OpenStreetMap station lat/lon fetched from the free Overpass
API. Distances are straight-line × 1.15 (empirical UK rail factor, ~5-10%
accuracy). Average elevation is fetched from OpenTopoData SRTM 30m (free,
no auth). All results are cached to backend/data/mileage/.coord_cache.json so
network calls happen only once per installation.

Expected mileage.json format:
{
    "segments": [
        {
            "from_tiploc": "WATRLMN",
            "to_tiploc": "CLPHMJN",
            "elr": "WAT1",
            "miles": 3,
            "chains": 45,
            "distance_miles": 3.5625
        },
        ...
    ]
}
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Empirical factor: UK rail distance ≈ straight-line × 1.15
_RAIL_FACTOR = 1.15
_EARTH_RADIUS_MILES = 3958.8
# OpenTopoData chunk size (max 100 locations per request on free tier)
_ELEVATION_CHUNK = 100


def chains_to_decimal_miles(miles: int, chains: int) -> float:
    """Convert miles and chains to decimal miles. 1 mile = 80 chains."""
    return miles + (chains / 80.0)


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two WGS84 lat/lon points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


class _CoordinateFallback:
    """Estimates rail distances and average elevation from OSM station coordinates.

    Uses the public Overpass API for lat/lon (no registration, no API key).
    Uses the public OpenTopoData SRTM 30m API for elevation (no registration).
    Results are cached locally so network is only hit once per installation.

    Cache format: { "CRS": [lat, lon, elev_m_or_null], ... }
    """

    OVERPASS_URL = "https://overpass-api.de/api/interpreter"
    OVERPASS_QUERY = (
        "[out:json][timeout:90];"
        'node["railway"="station"]["ref:crs"](49.5,-8.5,61,2);'
        "out body;"
    )
    TOPODATA_URL = "https://api.opentopodata.org/v1/srtm30m"

    def __init__(self, cache_path: Path) -> None:
        self._cache_path = cache_path
        # CRS (upper) → (lat, lon, elev_m or None)
        self._coords: dict[str, tuple[float, float, Optional[float]]] = {}
        self._loaded = False

    def load(self) -> None:
        """Load from local cache, or fetch from Overpass + OpenTopoData and save."""
        if self._cache_path.exists():
            try:
                raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
                loaded: dict[str, tuple[float, float, Optional[float]]] = {}
                for k, v in raw.items():
                    if isinstance(v, (list, tuple)) and len(v) >= 2:
                        lat, lon = float(v[0]), float(v[1])
                        elev = float(v[2]) if len(v) >= 3 and v[2] is not None else None
                        loaded[k] = (lat, lon, elev)
                self._coords = loaded
                self._loaded = bool(self._coords)
                logger.info("Coordinate cache loaded: %d rail stations", len(self._coords))
                return
            except Exception as exc:
                logger.warning("Could not read coordinate cache: %s — re-fetching", exc)

        self._fetch_and_cache()

    def _fetch_and_cache(self) -> None:
        import requests

        # --- Step 1: OSM station coordinates ---
        try:
            logger.info(
                "Fetching UK rail station coordinates from Overpass API "
                "(one-time download, ~500 KB) …"
            )
            resp = requests.post(
                self.OVERPASS_URL,
                data={"data": self.OVERPASS_QUERY},
                timeout=120,
            )
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
        except Exception as exc:
            logger.warning("Could not fetch station coordinates from Overpass: %s", exc)
            return

        coords: dict[str, tuple[float, float, Optional[float]]] = {}
        for el in elements:
            crs = (el.get("tags") or {}).get("ref:crs", "").strip().upper()
            lat = el.get("lat")
            lon = el.get("lon")
            if crs and lat is not None and lon is not None:
                coords[crs] = (float(lat), float(lon), None)

        if not coords:
            logger.warning("Overpass returned no rail stations — skipping elevation fetch")
            return

        logger.info("Overpass: %d stations found — fetching elevations …", len(coords))

        # --- Step 2: OpenTopoData elevation for each station ---
        crs_list = list(coords.keys())
        for chunk_start in range(0, len(crs_list), _ELEVATION_CHUNK):
            chunk = crs_list[chunk_start: chunk_start + _ELEVATION_CHUNK]
            locs = "|".join(f"{coords[c][0]},{coords[c][1]}" for c in chunk)
            try:
                r = requests.get(
                    self.TOPODATA_URL,
                    params={"locations": locs},
                    timeout=30,
                )
                r.raise_for_status()
                results = r.json().get("results", [])
                for crs_key, result in zip(chunk, results):
                    elev = result.get("elevation")
                    lat, lon, _ = coords[crs_key]
                    coords[crs_key] = (lat, lon, float(elev) if elev is not None else None)
            except Exception as exc:
                logger.warning(
                    "Elevation fetch failed for chunk starting at %d: %s", chunk_start, exc
                )
            # Polite delay between chunks to respect free-tier rate limits
            if chunk_start + _ELEVATION_CHUNK < len(crs_list):
                time.sleep(1)

        self._coords = coords
        self._loaded = True

        # Save cache as { "CRS": [lat, lon, elev_or_null] }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        serialisable = {k: [v[0], v[1], v[2]] for k, v in self._coords.items()}
        self._cache_path.write_text(json.dumps(serialisable), encoding="utf-8")
        logger.info(
            "Coordinate + elevation cache saved: %d stations → %s",
            len(self._coords), self._cache_path,
        )

    @property
    def is_ready(self) -> bool:
        return self._loaded

    def estimate_miles(self, crs1: str, crs2: str) -> Optional[float]:
        """Straight-line × rail factor distance in miles, or None if CRS unknown."""
        if not self._loaded:
            return None
        c1 = self._coords.get(crs1.upper())
        c2 = self._coords.get(crs2.upper())
        if not c1 or not c2:
            return None
        return round(_haversine_miles(c1[0], c1[1], c2[0], c2[1]) * _RAIL_FACTOR, 2)

    def avg_elevation_m(self, crs1: str, crs2: str) -> Optional[float]:
        """Average elevation in metres between two stations, or None if unavailable."""
        if not self._loaded:
            return None
        c1 = self._coords.get(crs1.upper())
        c2 = self._coords.get(crs2.upper())
        if not c1 or not c2:
            return None
        e1, e2 = c1[2], c2[2]
        if e1 is None or e2 is None:
            return None
        return round((e1 + e2) / 2.0, 1)

    def estimate_segment(self, crs1: str, crs2: str) -> dict:
        """Return distance (miles) and avg_elevation_m for a CRS pair."""
        return {
            "distance_miles": self.estimate_miles(crs1, crs2),
            "avg_elevation_m": self.avg_elevation_m(crs1, crs2),
        }


class MileageResolver:
    """Resolves point-to-point rail distances and segment elevations.

    Primary lookup: mileage.json (official NESA data).
    Fallback: OSM coordinate estimation (approximate, ±5–10%) + OpenTopoData elevation.

    Call set_crs_lookup(corpus.tiploc_to_crs) after init so the fallback can
    convert TIPLOCs (used internally) to CRS codes (used by OSM).
    """

    def __init__(self, mileage_path: str) -> None:
        self._path = mileage_path
        self._segments: dict[tuple[str, str], list[dict]] = {}
        self._direct: dict[tuple[str, str], float] = {}
        self._loaded = False
        self._tiploc_to_crs: Optional[Callable[[str], Optional[str]]] = None
        coord_cache = Path(mileage_path).parent / ".coord_cache.json"
        self._coord_fallback = _CoordinateFallback(coord_cache)

    def set_crs_lookup(self, fn: Callable[[str], Optional[str]]) -> None:
        """Register a TIPLOC→CRS resolver so the coordinate fallback works."""
        self._tiploc_to_crs = fn

    def _ensure_coord_fallback(self) -> None:
        if not self._coord_fallback.is_ready:
            self._coord_fallback.load()

    def load(self) -> None:
        """Load mileage data from JSON file."""
        path = Path(self._path)
        if not path.exists():
            logger.warning("Mileage data file not found at %s", self._path)
            self._loaded = False
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for seg in data.get("segments", []):
            from_t = (seg.get("from_tiploc") or "").strip().upper()
            to_t = (seg.get("to_tiploc") or "").strip().upper()
            if not from_t or not to_t:
                continue

            if "distance_miles" in seg:
                dist = float(seg["distance_miles"])
            elif "miles" in seg and "chains" in seg:
                dist = chains_to_decimal_miles(int(seg["miles"]), int(seg["chains"]))
            else:
                continue

            for a, b in ((from_t, to_t), (to_t, from_t)):
                key = (a, b)
                if key not in self._segments:
                    self._segments[key] = []
                self._segments[key].append({"elr": seg.get("elr", ""), "distance_miles": dist})
                if key not in self._direct:
                    self._direct[key] = dist

        self._loaded = True
        logger.info("Mileage data loaded: %d segment pairs", len(self._direct))

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def provenance(self) -> dict:
        return {
            "source": "Network Rail NESA / Rail Data Marketplace",
            "path": self._path,
            "loaded": self._loaded,
            "segment_pairs": len(self._direct),
            "coordinate_fallback": self._coord_fallback.is_ready,
        }

    def get_distance(
        self,
        from_tiploc: str,
        to_tiploc: str,
        preferred_elr: Optional[str] = None,
    ) -> Optional[float]:
        dist, _ = self.get_distance_with_method(from_tiploc, to_tiploc, preferred_elr)
        return dist

    def get_distance_with_method(
        self,
        from_tiploc: str,
        to_tiploc: str,
        preferred_elr: Optional[str] = None,
    ) -> tuple[Optional[float], str]:
        """Return (distance_miles, method_label).

        Resolution order:
        1. ELR-preferred match in mileage.json
        2. Direct TIPLOC pair lookup in mileage.json
        3. Coordinate estimation via OSM (approximate)
        """
        from_t = from_tiploc.strip().upper()
        to_t = to_tiploc.strip().upper()
        key = (from_t, to_t)

        if self._loaded:
            if preferred_elr and key in self._segments:
                for seg in self._segments[key]:
                    if seg["elr"].upper() == preferred_elr.upper():
                        return seg["distance_miles"], f"elr_match:{preferred_elr}"
            if key in self._direct:
                return self._direct[key], "direct_lookup"

        if self._tiploc_to_crs is not None:
            self._ensure_coord_fallback()
            if self._coord_fallback.is_ready:
                crs_from = self._tiploc_to_crs(from_t)
                crs_to = self._tiploc_to_crs(to_t)
                if crs_from and crs_to:
                    est = self._coord_fallback.estimate_miles(crs_from, crs_to)
                    if est is not None:
                        return est, "coordinate_estimate"

        return None, "not_found" if self._loaded else "mileage_data_not_loaded"

    def get_segment_with_elevation(
        self,
        from_tiploc: str,
        to_tiploc: str,
        preferred_elr: Optional[str] = None,
    ) -> tuple[Optional[float], Optional[float], str]:
        """Return (distance_miles, avg_elevation_m, method_label).

        Elevation is only available when the coordinate fallback is used.
        For mileage.json lookups, elevation will be None unless the coordinate
        fallback is also ready (in which case we fetch elevation separately).
        """
        dist, method = self.get_distance_with_method(from_tiploc, to_tiploc, preferred_elr)
        elev: Optional[float] = None

        if self._tiploc_to_crs is not None:
            self._ensure_coord_fallback()
            if self._coord_fallback.is_ready:
                crs_from = self._tiploc_to_crs(from_tiploc.strip().upper())
                crs_to = self._tiploc_to_crs(to_tiploc.strip().upper())
                if crs_from and crs_to:
                    elev = self._coord_fallback.avg_elevation_m(crs_from, crs_to)

        return dist, elev, method

    def estimate_by_crs(self, crs1: str, crs2: str) -> dict:
        """Coordinate estimate for a CRS pair (used by the recalculate endpoint).

        Returns {"distance_miles": float|None, "avg_elevation_m": float|None}.
        """
        self._ensure_coord_fallback()
        return self._coord_fallback.estimate_segment(crs1, crs2)
