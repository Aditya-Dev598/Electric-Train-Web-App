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
        self._by_crs: dict[str, StationMapping] = {}
        self._by_tiploc: dict[str, StationMapping] = {}
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
            crs = (entry.get("CRS") or "").strip()
            name = (entry.get("NLCDESC") or "").strip()
            nlc = (entry.get("NLC") or "").strip()
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

            if crs:
                self._by_crs[crs.upper()] = mapping

            if name:
                key = name.upper()
                if key not in self._by_name:
                    self._by_name[key] = []
                self._by_name[key].append(mapping)

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

        Tries exact match in order: CRS code, TIPLOC, station name.
        Returns None if no match found. Returns list for name matches
        (may be ambiguous).
        """
        if not self._loaded:
            logger.error("CORPUS not loaded, cannot resolve station")
            return None

        q = query.strip().upper()
        if not q:
            return None

        # 1. Try CRS exact match (3 letters)
        if len(q) == 3 and q in self._by_crs:
            return [self._by_crs[q]]

        # 2. Try TIPLOC exact match
        if q in self._by_tiploc:
            return [self._by_tiploc[q]]

        # 3. Try station name exact match
        if q in self._by_name:
            return self._by_name[q]

        return None

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
