"""Mileage resolver: lookup official rail distances between stations.

Data source: Network Rail NESA / Rail Data Marketplace official mileage datasets.
Mileage data is in miles and chains (1 mile = 80 chains).

Expected data format (JSON):
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
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def chains_to_decimal_miles(miles: int, chains: int) -> float:
    """Convert miles and chains to decimal miles. 1 mile = 80 chains."""
    return miles + (chains / 80.0)


class MileageResolver:
    """Resolves point-to-point rail distances using official mileage data."""

    def __init__(self, mileage_path: str) -> None:
        self._path = mileage_path
        # Keyed by (from_tiploc, to_tiploc) -> list of segments with ELR
        self._segments: dict[tuple[str, str], list[dict]] = {}
        # Keyed by (from_tiploc, to_tiploc) -> direct distance
        self._direct: dict[tuple[str, str], float] = {}
        self._loaded = False

    def load(self) -> None:
        """Load the mileage data from JSON file."""
        path = Path(self._path)
        if not path.exists():
            logger.warning("Mileage data file not found at %s", self._path)
            self._loaded = False
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        segments = data.get("segments", [])
        for seg in segments:
            from_t = (seg.get("from_tiploc") or "").strip().upper()
            to_t = (seg.get("to_tiploc") or "").strip().upper()

            if not from_t or not to_t:
                continue

            # Pre-compute decimal miles if not present
            if "distance_miles" in seg:
                dist = float(seg["distance_miles"])
            elif "miles" in seg and "chains" in seg:
                dist = chains_to_decimal_miles(int(seg["miles"]), int(seg["chains"]))
            else:
                continue

            key = (from_t, to_t)
            if key not in self._segments:
                self._segments[key] = []
            self._segments[key].append({
                "elr": seg.get("elr", ""),
                "distance_miles": dist,
            })

            # Also store direct lookup (use first/shortest by default)
            if key not in self._direct:
                self._direct[key] = dist

            # Store reverse direction too (distance is the same)
            rev_key = (to_t, from_t)
            if rev_key not in self._segments:
                self._segments[rev_key] = []
            self._segments[rev_key].append({
                "elr": seg.get("elr", ""),
                "distance_miles": dist,
            })
            if rev_key not in self._direct:
                self._direct[rev_key] = dist

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
        }

    def get_distance(
        self,
        from_tiploc: str,
        to_tiploc: str,
        preferred_elr: Optional[str] = None,
    ) -> Optional[float]:
        """Get the rail distance in miles between two stations.

        Args:
            from_tiploc: Origin TIPLOC
            to_tiploc: Destination TIPLOC
            preferred_elr: Optional ELR to prefer when multiple paths exist

        Returns:
            Distance in decimal miles, or None if not found.
        """
        if not self._loaded:
            return None

        key = (from_tiploc.strip().upper(), to_tiploc.strip().upper())

        # Try with preferred ELR first
        if preferred_elr and key in self._segments:
            for seg in self._segments[key]:
                if seg["elr"].upper() == preferred_elr.upper():
                    return seg["distance_miles"]

        # Fall back to direct lookup
        return self._direct.get(key)

    def get_distance_with_method(
        self,
        from_tiploc: str,
        to_tiploc: str,
        preferred_elr: Optional[str] = None,
    ) -> tuple[Optional[float], str]:
        """Get distance and resolution method.

        Returns (distance, method) tuple where method describes how the
        distance was resolved for audit trail.
        """
        if not self._loaded:
            return None, "mileage_data_not_loaded"

        key = (from_tiploc.strip().upper(), to_tiploc.strip().upper())

        if preferred_elr and key in self._segments:
            for seg in self._segments[key]:
                if seg["elr"].upper() == preferred_elr.upper():
                    return seg["distance_miles"], f"elr_match:{preferred_elr}"

        if key in self._direct:
            return self._direct[key], "direct_lookup"

        return None, "not_found"
