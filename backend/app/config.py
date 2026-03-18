"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Immutable application settings sourced from env vars / config files."""

    # --- Data file paths ---
    cif_data_path: str = os.getenv("CIF_DATA_PATH", "data/cif")
    corpus_data_path: str = os.getenv("CORPUS_DATA_PATH", "data/corpus/CORPUSExtract.json")
    mileage_data_path: str = os.getenv("MILEAGE_DATA_PATH", "data/mileage/mileage.json")

    # --- Darwin API ---
    darwin_api_url: str = os.getenv(
        "DARWIN_API_URL",
        "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ldb12.asmx",
    )
    darwin_api_token: str = os.getenv("DARWIN_API_TOKEN", "")
    darwin_timeout_seconds: int = int(os.getenv("DARWIN_TIMEOUT_SECONDS", "10"))
    darwin_max_retries: int = int(os.getenv("DARWIN_MAX_RETRIES", "2"))

    # --- Application ---
    max_date_range_days: int = int(os.getenv("MAX_DATE_RANGE_DAYS", "31"))
    allowed_operators: list[str] = field(default_factory=list)
    cors_origins: list[str] = field(default_factory=lambda: os.getenv(
        "CORS_ORIGINS", "http://localhost:3000"
    ).split(","))
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
    max_request_size_bytes: int = int(os.getenv("MAX_REQUEST_SIZE_BYTES", str(1024 * 1024)))

    # --- Output ---
    output_dir: str = os.getenv("OUTPUT_DIR", "output")

    # --- Logging ---
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def __post_init__(self) -> None:
        if not self.allowed_operators:
            raw = os.getenv("ALLOWED_OPERATORS", "")
            ops = [o.strip().upper() for o in raw.split(",") if o.strip()]
            object.__setattr__(self, "allowed_operators", ops)

        Path(self.output_dir).mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Return a singleton-like Settings instance."""
    return Settings()
