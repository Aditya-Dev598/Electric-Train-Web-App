"""FastAPI application entry point.

Initializes all data sources, configures middleware, and mounts routes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.routers import cif, electric, health, solar, timetable
from backend.app.security.middleware import (
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from backend.app.services.audit_logger import AuditLogger
from backend.app.services.cif_parser import CIFParser
from backend.app.services.corpus_mapper import CorpusMapper
from backend.app.services.darwin_enricher import DarwinEnricher
from backend.app.services.mileage_resolver import MileageResolver
from backend.app.services.orchestrator import Orchestrator
from backend.app.services.result_store import ResultStore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

_cif_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cif-loader")


def _load_cif_from_disk(cif_parser: CIFParser, cif_data_path: str) -> None:
    """Blocking CIF load — runs in a background thread so startup is instant."""
    uploaded = Path(cif_data_path) / "uploaded.CIF"
    try:
        if uploaded.is_file():
            cif_parser.parse_file(str(uploaded))
            logger.info("CIF preloaded from uploaded.CIF: %d schedules", len(cif_parser.schedules))
        elif os.path.isdir(cif_data_path):
            cif_parser.parse_directory(cif_data_path)
            logger.info("CIF loaded from directory: %d schedules", len(cif_parser.schedules))
        elif os.path.isfile(cif_data_path):
            cif_parser.parse_file(cif_data_path)
            logger.info("CIF loaded: %d schedules", len(cif_parser.schedules))
        else:
            logger.info("No CIF file found — upload one via the web UI")
    except Exception as exc:
        logger.warning("CIF background load failed: %s", exc)


def create_app() -> FastAPI:
    """Application factory: create and configure the FastAPI app."""
    settings = get_settings()

    # --- Initialise data sources (fast, synchronous) ---
    audit = AuditLogger()

    corpus = CorpusMapper(settings.corpus_data_path)
    try:
        corpus.load()
        logger.info("CORPUS loaded successfully")
    except Exception as exc:
        logger.warning("Failed to load CORPUS: %s", exc)

    # CIF parser starts empty; the actual file is loaded in the background
    # startup task so the server is reachable immediately even for large files.
    cif_parser = CIFParser()

    mileage = MileageResolver(settings.mileage_data_path)
    try:
        mileage.load()
        logger.info("Mileage data loaded successfully")
    except Exception as exc:
        logger.warning("Failed to load mileage data: %s", exc)
    # Wire up TIPLOC→CRS resolver so the coordinate fallback can look up station coords.
    # Pre-load the bundled coordinate file so the lookup can prefer variants whose CRS
    # is actually present in the database (e.g. CRDFCEN→CDF over CRDFBUS→CCB).
    mileage._coord_fallback.load()
    _known_coords: set = set(mileage._coord_fallback._coords.keys())

    def _tiploc_to_crs_with_variants(tiploc: str):
        """Resolve TIPLOC → CRS, preferring variants that have coordinate data.

        Resolution order:
        1. Direct TIPLOC→CRS from CORPUS (skip Z/X internal codes)
        2. Iterate 4-char prefix variants (WATRLMN→WATRLOO→WAT), prefer the
           variant whose CRS exists in the coordinate database; fall back to
           the first valid CRS found if none has coordinates.
        """
        crs = corpus.tiploc_to_crs(tiploc)
        if crs and not crs.startswith(("Z", "X")):
            return crs
        # Try prefix variants — collect first valid CRS and first with known coords
        first_valid: str | None = None
        for variant in corpus.tiploc_variants(tiploc):
            crs = corpus.tiploc_to_crs(variant)
            if crs and not crs.startswith(("Z", "X")):
                if not first_valid:
                    first_valid = crs
                if crs.upper() in _known_coords:
                    return crs  # best match: variant is in the coordinate DB
        return first_valid  # fallback: first valid CRS even if not in DB

    mileage.set_crs_lookup(_tiploc_to_crs_with_variants)

    darwin = DarwinEnricher(
        api_url=settings.darwin_api_url,
        api_token=settings.darwin_api_token,
        timeout=settings.darwin_timeout_seconds,
        max_retries=settings.darwin_max_retries,
        audit=audit,
    )

    orchestrator = Orchestrator(
        corpus=corpus,
        cif_parser=cif_parser,
        mileage=mileage,
        darwin=darwin,
        audit=audit,
    )

    result_store = ResultStore(settings.results_data_path)

    # --- Lifespan: kick off CIF loading after server is ready ---
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        logger.info("Starting CIF background load (server already accepting connections)…")
        cif_future = loop.run_in_executor(
            _cif_executor,
            _load_cif_from_disk,
            cif_parser,
            settings.cif_data_path,
        )
        yield  # server is up and accepting connections while CIF loads
        # On shutdown, give the loader a moment to finish cleanly
        try:
            await asyncio.wait_for(asyncio.shield(cif_future), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass

    # --- Build app ---
    app = FastAPI(
        lifespan=lifespan,
        title="UK Rail Timetable Generator",
        description=(
            "Generate railway timetable and route CSV outputs using "
            "official UK rail data sources (CIF, CORPUS, NESA, Darwin)."
        ),
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # Middleware (order matters: last added = first executed)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=settings.max_request_size_bytes,
    )
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=settings.rate_limit_per_minute,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
        max_age=3600,
    )

    # Store state
    app.state.corpus = corpus
    app.state.cif_parser = cif_parser
    app.state.mileage = mileage
    app.state.darwin = darwin
    app.state.orchestrator = orchestrator
    app.state.audit = audit
    app.state.result_store = result_store
    app.state.settings = settings

    # Routes
    app.include_router(cif.router)
    app.include_router(timetable.router)
    app.include_router(electric.router)
    app.include_router(solar.router)
    app.include_router(health.router)

    return app


app = create_app()
