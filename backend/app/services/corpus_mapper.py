"""CORPUS mapper: resolve station names to CRS/TIPLOC using Network Rail CORPUS data.

Data source: Network Rail CORPUS extract (JSON format).
Download from: https://datafeeds.networkrail.co.uk/ (requires login)
Expected format: {"TIPLOCDATA": [{"TIPLOC": "...", "CRS": "...", "NLCDESC": "...", ...}, ...]}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from backend.app.models import StationMapping

logger = logging.getLogger(__name__)


class CorpusMapper:
    """Resolves station names and codes using Network Rail CORPUS data.

    Performs EXACT matching only - no fuzzy or partial matches.
    """

    def __init__(self, corpus_path: str) -> None:
        self._path = corpus_path
        self._by_name: dict[str, list[StationMapping]] = {}
        self._by_crs: dict[str, list[StationMapping]] = {}
        self._by_tiploc: dict[str, StationMapping] = {}
        self._by_prefix: dict[str, list[str]] = {}  # 4-char prefix → list of TIPLOCs
        self._loaded = False
        self._file_date: Optional[str] = None

    def load(self) -> None:
        """Load and index the CORPUS JSON file."""
        path = Path(self._path)
        if not path.exists():
            logger.warning("CORPUS file not found at %s", self._path)
            self._loaded = False
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        tiploc_data = data.get("TIPLOCDATA", [])
        if not tiploc_data:
            logger.warning("CORPUS file has no TIPLOCDATA entries")
            self._loaded = False
            return

        for entry in tiploc_data:
            tiploc = (entry.get("TIPLOC") or "").strip()
            crs = (entry.get("3ALPHA") or entry.get("CRS") or "").strip()
            name = (entry.get("NLCDESC") or "").strip()
            nlc = str(entry.get("NLC") or "").strip()
            stanox = (entry.get("STANOX") or "").strip()

            if not tiploc:
                continue

            mapping = StationMapping(
                station_name=name,
                crs_code=crs,
                tiploc=tiploc,
                nlc=nlc,
                stanox=stanox,
            )

            self._by_tiploc[tiploc.upper()] = mapping

            # 4-char prefix index — groups WATRLOO, WATRLMN, WATRLOW etc. under "WATR"
            prefix = tiploc.upper()[:4]
            if len(prefix) == 4:
                self._by_prefix.setdefault(prefix, []).append(tiploc.upper())

            if crs:
                self._by_crs.setdefault(crs.upper(), []).append(mapping)

            if name:
                key = name.upper()
                if key not in self._by_name:
                    self._by_name[key] = []
                self._by_name[key].append(mapping)

                # Also index under apostrophe-normalised name for fuzzy lookup
                norm = key.replace("'", "").replace("`", "")
                if norm != key:
                    if norm not in self._by_name:
                        self._by_name[norm] = []
                    self._by_name[norm].append(mapping)

        self._loaded = True
        logger.info("CORPUS loaded: %d TIPLOCs, %d CRS codes, %d named stations",
                     len(self._by_tiploc), len(self._by_crs), len(self._by_name))

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def provenance(self) -> dict:
        return {
            "source": "Network Rail CORPUS",
            "path": self._path,
            "loaded": self._loaded,
            "tiploc_count": len(self._by_tiploc),
            "crs_count": len(self._by_crs),
        }

    def resolve_station(self, query: str) -> Optional[list[StationMapping]]:
        """Resolve a station query to StationMapping(s).

        Resolution order:
        1. CRS code exact match (e.g. "WAT", "VIC")
        2. TIPLOC exact match
        3. Station name exact match
        4. Name + " LONDON" suffix (e.g. "Waterloo" → "WATERLOO LONDON")
        5. Apostrophe-normalised name + " LONDON" (e.g. "Kings Cross")
        6. Starts-with partial match on passenger stations (has CRS, not LT/Z-code),
           sorted by name length (shortest = best match); handles "Clapham Junction"
           matching "CLAPHAM JUNCTION LONDON" etc.

        Returns None if no match found. Returns list for name matches
        (first entry is best match; multiple entries indicate ambiguity).
        """
        if not self._loaded:
            logger.error("CORPUS not loaded, cannot resolve station")
            return None

        q = query.strip().upper()
        if not q:
            return None

        # 1. CRS exact match (2-4 letters to cover edge cases)
        if 2 <= len(q) <= 4 and q in self._by_crs:
            return self._by_crs[q]

        # 2. TIPLOC exact match
        if q in self._by_tiploc:
            return [self._by_tiploc[q]]

        # 3. Station name exact match
        if q in self._by_name:
            return self._by_name[q]

        # Normalise apostrophes for steps 3.5-6
        q_norm = q.replace("'", "").replace("`", "")

        # 3.5. "LONDON X" → try "X LONDON" (handles "London Waterloo" → "WATERLOO LONDON",
        #      "London Victoria" → "VICTORIA LONDON", "London Kings Cross" → "KINGS CROSS LONDON")
        if q_norm.startswith("LONDON "):
            q_swapped = q_norm[7:].strip() + " LONDON"
            if q_swapped in self._by_name:
                matches = [m for m in self._by_name[q_swapped] if m.crs_code]
                if matches:
                    return matches

        # 4. Try adding " LONDON" — covers the many "WATERLOO LONDON", "VICTORIA LONDON" etc.
        q_london = q_norm + " LONDON"
        if q_london in self._by_name:
            matches = [m for m in self._by_name[q_london] if m.crs_code]
            if matches:
                return matches

        # 5. Starts-with partial match (passenger stations only, not LT/Underground Z-codes)
        candidates: list[tuple[int, StationMapping]] = []
        seen_crs: set[str] = set()
        for name, mappings in self._by_name.items():
            name_norm = name.replace("'", "").replace("`", "")
            if name_norm.startswith(q_norm):
                for m in mappings:
                    if m.crs_code and not m.crs_code.startswith("Z") and m.crs_code not in seen_crs:
                        candidates.append((len(name), m))
                        seen_crs.add(m.crs_code)

        if candidates:
            # Shortest name = most specific match (e.g. "WOKING" before "WOKING JUNCTION")
            candidates.sort(key=lambda x: x[0])
            return [m for _, m in candidates]

        return None

    def tiploc_variants(self, tiploc: str) -> list[str]:
        """Return all CORPUS TIPLOCs that share the same 4-char prefix.

        Used to expand a single resolved TIPLOC to all physical variants of
        the same station, e.g. WATRLOO → [WATRLOO, WATRLMN, WATRLOW, WATR]
        so that CIF schedule filtering doesn't miss platform-level TIPLOCs.
        """
        if not self._loaded:
            return [tiploc.upper()]
        prefix = tiploc.upper()[:4]
        return self._by_prefix.get(prefix, [tiploc.upper()])

    def tiploc_to_crs(self, tiploc: str) -> Optional[str]:
        """Resolve a TIPLOC to its CRS code. Returns None if not found."""
        if not self._loaded:
            return None
        mapping = self._by_tiploc.get(tiploc.strip().upper())
        if mapping and mapping.crs_code:
            return mapping.crs_code
        return None

    def tiploc_to_name(self, tiploc: str) -> Optional[str]:
        """Resolve a TIPLOC to station name. Returns None if not found."""
        if not self._loaded:
            return None
        mapping = self._by_tiploc.get(tiploc.strip().upper())
        if mapping and mapping.station_name:
            return mapping.station_name
        return None

    def is_passenger_station(self, tiploc: str) -> bool:
        """Check if a TIPLOC represents a public passenger station.

        Passenger stations have a CRS code in CORPUS.
        Junctions, depots, and non-public timing points typically don't.
        """
        if not self._loaded:
            return False
        mapping = self._by_tiploc.get(tiploc.strip().upper())
        if not mapping:
            return False
        return bool(mapping.crs_code)
