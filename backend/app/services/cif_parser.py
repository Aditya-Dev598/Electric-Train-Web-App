"""CIF (Common Interface Format) parser for Network Rail timetable data.

Parses fixed-width MCA/CIF files containing schedule records.
Record types handled: HD, BS, BX, LO, LI, LT.

Data source: Network Rail CIF/TTIS data feed.
Download from: https://datafeeds.networkrail.co.uk/ or Rail Data Marketplace.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from backend.app.models import CIFLocation, CIFSchedule, STPIndicator

logger = logging.getLogger(__name__)


def _safe_strip(text: str, start: int, end: int) -> str:
    """Safely extract and strip a substring from fixed-width record."""
    if len(text) < end:
        return text[start:].strip() if len(text) > start else ""
    return text[start:end].strip()


def _parse_cif_date(date_str: str) -> Optional[date]:
    """Parse a CIF date in YYMMDD format."""
    cleaned = date_str.strip()
    if not cleaned or len(cleaned) != 6:
        return None
    try:
        return datetime.strptime(cleaned, "%y%m%d").date()
    except ValueError:
        return None


class CIFParser:
    """Parses CIF/MCA fixed-width timetable files into structured schedule objects."""

    def __init__(self) -> None:
        self._schedules: list[CIFSchedule] = []
        self._header_date: Optional[str] = None
        self._file_path: Optional[str] = None
        self._total_records = 0

    @property
    def schedules(self) -> list[CIFSchedule]:
        return list(self._schedules)

    @property
    def provenance(self) -> dict:
        return {
            "source": "Network Rail CIF",
            "path": self._file_path or "",
            "header_date": self._header_date or "",
            "total_schedules": len(self._schedules),
            "total_records_parsed": self._total_records,
        }

    def parse_lines(self, lines, source: str = "<upload>") -> list[CIFSchedule]:
        """Parse CIF data from an iterable of text lines.

        Use this for uploaded file content. Each line should be a raw text line
        (with or without trailing newline — both are handled).
        """
        self._file_path = source
        self._schedules = []
        self._total_records = 0
        current_schedule: Optional[CIFSchedule] = None

        for line in lines:
            self._total_records += 1
            line = line.rstrip("\n\r")

            if len(line) < 2:
                continue

            record_type = line[:2]

            if record_type == "HD":
                self._parse_header(line)
            elif record_type == "BS":
                if current_schedule is not None:
                    self._schedules.append(current_schedule)
                current_schedule = self._parse_bs(line)
            elif record_type == "BX":
                if current_schedule is not None:
                    self._parse_bx(line, current_schedule)
            elif record_type == "LO":
                if current_schedule is not None:
                    loc = self._parse_lo(line)
                    if loc:
                        current_schedule.locations.append(loc)
            elif record_type == "LI":
                if current_schedule is not None:
                    loc = self._parse_li(line)
                    if loc:
                        current_schedule.locations.append(loc)
            elif record_type == "LT":
                if current_schedule is not None:
                    loc = self._parse_lt(line)
                    if loc:
                        current_schedule.locations.append(loc)
            elif record_type == "ZZ":
                break

        if current_schedule is not None:
            self._schedules.append(current_schedule)

        logger.info("CIF parsed: %d records, %d schedules from %s",
                    self._total_records, len(self._schedules), source)
        return self._schedules

    def parse_file(self, file_path: str) -> list[CIFSchedule]:
        """Parse a CIF/MCA file and return all schedules."""
        path = Path(file_path)
        if not path.exists():
            logger.warning("CIF file not found: %s", file_path)
            return []

        # Detect Git LFS pointer files (not the real CIF data)
        with open(path, "r", encoding="utf-8", errors="replace") as _f:
            first_line = _f.readline().rstrip()
        if first_line.startswith("version https://git-lfs.github.com"):
            logger.error(
                "CIF file '%s' is a Git LFS pointer, not the actual timetable data. "
                "Run 'git lfs pull' to download the real file.",
                file_path,
            )
            return []

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            self.parse_lines(f, source=file_path)

        return self._schedules

    def parse_directory(self, dir_path: str) -> list[CIFSchedule]:
        """Parse all .mca and .cif files in a directory."""
        path = Path(dir_path)
        if not path.is_dir():
            logger.warning("CIF directory not found: %s", dir_path)
            return []

        all_schedules: list[CIFSchedule] = []
        for ext in ("*.mca", "*.MCA", "*.cif", "*.CIF"):
            for f in sorted(path.glob(ext)):
                schedules = self.parse_file(str(f))
                all_schedules.extend(schedules)

        self._schedules = all_schedules
        return all_schedules

    def _parse_header(self, line: str) -> None:
        """Parse HD (Header) record for provenance."""
        # HD record: col 2-9 = file mainframe identity
        # col 22-27 = date of extract (DDMMYY)
        self._header_date = _safe_strip(line, 22, 28)

    def _parse_bs(self, line: str) -> Optional[CIFSchedule]:
        """Parse BS (Basic Schedule) record.

        Fixed-width layout:
        Col 0-1:   Record type "BS"
        Col 2:     Transaction type (N=new, D=delete, R=revise)
        Col 3-8:   Train UID (6 chars)
        Col 9-14:  Date runs from (YYMMDD)
        Col 15-20: Date runs to (YYMMDD)
        Col 21-27: Days run (7 chars: Mon-Sun, 1=runs 0=doesn't)
        Col 28:    Bank holiday running
        Col 29:    Train status (P=passenger, F=freight, etc.)
        Col 30-31: Train category
        Col 32-35: Train identity (headcode)
        Col 79:    STP indicator (C/N/O/P)
        """
        transaction = _safe_strip(line, 2, 3)
        if transaction == "D":
            # Delete transaction - we skip these, they're handled by STP
            return None

        train_uid = _safe_strip(line, 3, 9)
        date_from = _parse_cif_date(_safe_strip(line, 9, 15))
        date_to = _parse_cif_date(_safe_strip(line, 15, 21))
        days_run = _safe_strip(line, 21, 28)
        bank_holiday = _safe_strip(line, 28, 29)
        train_status = _safe_strip(line, 29, 30)
        train_category = _safe_strip(line, 30, 32)
        train_identity = _safe_strip(line, 32, 36)

        # STP indicator at col 79
        stp_raw = _safe_strip(line, 79, 80) if len(line) > 79 else "P"
        try:
            stp = STPIndicator(stp_raw)
        except ValueError:
            stp = STPIndicator.PERMANENT

        if not train_uid or date_from is None or date_to is None:
            return None

        # Pad days_run to 7 chars
        if len(days_run) < 7:
            days_run = days_run.ljust(7, "0")

        return CIFSchedule(
            train_uid=train_uid,
            date_runs_from=date_from,
            date_runs_to=date_to,
            days_run=days_run,
            stp_indicator=stp,
            train_status=train_status,
            train_category=train_category,
            train_identity=train_identity,
            bank_holiday_running=bank_holiday,
        )

    def _parse_bx(self, line: str, schedule: CIFSchedule) -> None:
        """Parse BX (Basic Schedule Extra Detail) record.

        Col 0-1:  Record type "BX"
        Col 11-12: ATOC code (2 chars)
        Col 13:    Applicable timetable code
        """
        schedule.atoc_code = _safe_strip(line, 11, 13)
        schedule.applicable_timetable = _safe_strip(line, 13, 14)

    def _parse_lo(self, line: str) -> Optional[CIFLocation]:
        """Parse LO (Location Origin) record.

        Col 0-1:  Record type "LO"
        Col 2-9:  TIPLOC (8 chars, right-padded)
        Col 10-14: Scheduled departure (HHMM + half-min)
        Col 15-18: Public departure (HHMM)
        Col 19-21: Platform
        Col 29-40: Activity
        """
        tiploc = _safe_strip(line, 2, 10)
        sched_dep = _safe_strip(line, 10, 15)
        public_dep = _safe_strip(line, 15, 19)
        platform = _safe_strip(line, 19, 22)
        activity = _safe_strip(line, 29, 41)

        if not tiploc:
            return None

        # Public departure of "0000" usually means not set
        if public_dep == "0000":
            public_dep = ""

        return CIFLocation(
            record_type="LO",
            tiploc=tiploc,
            scheduled_departure=sched_dep,
            public_departure=public_dep or None,
            platform=platform or None,
            activity=activity,
        )

    def _parse_li(self, line: str) -> Optional[CIFLocation]:
        """Parse LI (Location Intermediate) record.

        Col 0-1:   Record type "LI"
        Col 2-9:   TIPLOC (8 chars)
        Col 10-14:  Scheduled arrival (HHMM + half)
        Col 15-19:  Scheduled departure (HHMM + half)
        Col 20-24:  Scheduled pass (HHMM + half)
        Col 25-28:  Public arrival (HHMM)
        Col 29-32:  Public departure (HHMM)
        Col 33-35:  Platform
        Col 42-53:  Activity
        """
        tiploc = _safe_strip(line, 2, 10)
        sched_arr = _safe_strip(line, 10, 15)
        sched_dep = _safe_strip(line, 15, 20)
        sched_pass = _safe_strip(line, 20, 25)
        public_arr = _safe_strip(line, 25, 29)
        public_dep = _safe_strip(line, 29, 33)
        platform = _safe_strip(line, 33, 36)
        activity = _safe_strip(line, 42, 54)

        if not tiploc:
            return None

        # Clean up "0000" public times
        if public_arr == "0000":
            public_arr = ""
        if public_dep == "0000":
            public_dep = ""

        # If it's a pass-through, use pass time as arrival/departure
        final_arr = sched_arr if sched_arr else sched_pass
        final_dep = sched_dep if sched_dep else sched_pass

        return CIFLocation(
            record_type="LI",
            tiploc=tiploc,
            scheduled_arrival=final_arr or None,
            scheduled_departure=final_dep or None,
            public_arrival=public_arr or None,
            public_departure=public_dep or None,
            platform=platform or None,
            activity=activity,
        )

    def _parse_lt(self, line: str) -> Optional[CIFLocation]:
        """Parse LT (Location Terminus) record.

        Col 0-1:  Record type "LT"
        Col 2-9:  TIPLOC (8 chars)
        Col 10-14: Scheduled arrival (HHMM + half)
        Col 15-18: Public arrival (HHMM)
        Col 19-21: Platform
        Col 25-36: Activity
        """
        tiploc = _safe_strip(line, 2, 10)
        sched_arr = _safe_strip(line, 10, 15)
        public_arr = _safe_strip(line, 15, 19)
        platform = _safe_strip(line, 19, 22)
        activity = _safe_strip(line, 25, 37)

        if not tiploc:
            return None

        if public_arr == "0000":
            public_arr = ""

        return CIFLocation(
            record_type="LT",
            tiploc=tiploc,
            scheduled_arrival=sched_arr or None,
            public_arrival=public_arr or None,
            platform=platform or None,
            activity=activity,
        )
