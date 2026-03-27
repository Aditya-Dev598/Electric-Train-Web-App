"""Persistent file-system result storage.

Results are stored under:
    backend/data/results/{gen_id}/
        timetable.csv
        route.csv
        debug.csv
        metadata.json

Survives backend restarts. Provides list, load, save, delete operations.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional


class ResultStore:
    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        gen_id: str,
        timetable_csv: str,
        route_csv: str,
        debug_csv: str,
        metadata: dict,
    ) -> None:
        d = self._base / gen_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "timetable.csv").write_text(timetable_csv, encoding="utf-8")
        (d / "route.csv").write_text(route_csv, encoding="utf-8")
        (d / "debug.csv").write_text(debug_csv, encoding="utf-8")
        (d / "metadata.json").write_text(
            json.dumps(metadata, default=str), encoding="utf-8"
        )

    def load_csv(self, gen_id: str, csv_type: str) -> Optional[str]:
        """Return CSV text or None if not found. csv_type: 'timetable'|'route'|'debug'"""
        path = self._base / gen_id / f"{csv_type}.csv"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_csv(self, gen_id: str, csv_type: str, content: str) -> None:
        """Overwrite a CSV on disk (used by the in-browser editor PUT endpoint)."""
        path = self._base / gen_id / f"{csv_type}.csv"
        if not path.parent.exists():
            raise FileNotFoundError(f"Result {gen_id} does not exist")
        path.write_text(content, encoding="utf-8")

    def load_metadata(self, gen_id: str) -> Optional[dict]:
        path = self._base / gen_id / "metadata.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_results(self) -> list[dict]:
        """Return metadata for all stored results, newest first."""
        results = []
        if not self._base.exists():
            return results
        for d in sorted(
            self._base.iterdir(),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            if d.is_dir():
                meta = self.load_metadata(d.name)
                if meta:
                    results.append({"generation_id": d.name, **meta})
        return results

    def delete(self, gen_id: str) -> bool:
        d = self._base / gen_id
        if d.exists():
            shutil.rmtree(d)
            return True
        return False

    def exists(self, gen_id: str) -> bool:
        return (self._base / gen_id).is_dir()
