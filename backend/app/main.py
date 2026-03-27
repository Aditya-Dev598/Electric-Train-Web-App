"""FastAPI application entry point.

Initializes all data sources, configures middleware, and mounts routes.
"""

from __future__ import annotations

import logging
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import get_settings
from backend.app.routers import cif, electric, health, timetable
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


def create_app() -> FastAPI:
    """Application factory: create and configure the FastAPI app."""
    settings = get_settings()

    app = FastAPI(
        title="UK Rail Timetable Generator",
        description=(
            "Generate railway timetable and route CSV outputs using "
            "official UK rail data sources (CIF, CORPUS, NESA, Darwin)."
        ),
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # --- Middleware (order matters: last added = first executed) ---
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

    # --- Initialize data sources ---
    audit = AuditLogger()

    # CORPUS
    corpus = CorpusMapper(settings.corpus_data_path)
    try:
        corpus.load()
        logger.info("CORPUS loaded successfully")
    except Exception as exc:
        logger.warning("Failed to load CORPUS: %s", exc)

    # CIF
    cif_parser = CIFParser()
    try:
        if os.path.isdir(settings.cif_data_path):
            cif_parser.parse_directory(settings.cif_data_path)
        elif os.path.isfile(settings.cif_data_path):
            cif_parser.parse_file(settings.cif_data_path)
        logger.info("CIF loaded: %d schedules", len(cif_parser.schedules))
    except Exception as exc:
        logger.warning("Failed to load CIF data: %s", exc)

    # Mileage
    mileage = MileageResolver(settings.mileage_data_path)
    try:
        mileage.load()
        logger.info("Mileage data loaded successfully")
    except Exception as exc:
        logger.warning("Failed to load mileage data: %s", exc)

    # Darwin
    darwin = DarwinEnricher(
        api_url=settings.darwin_api_url,
        api_token=settings.darwin_api_token,
        timeout=settings.darwin_timeout_seconds,
        max_retries=settings.darwin_max_retries,
        audit=audit,
    )

    # Orchestrator
    orchestrator = Orchestrator(
        corpus=corpus,
        cif_parser=cif_parser,
        mileage=mileage,
        darwin=darwin,
        audit=audit,
    )

    # Result store (persists across restarts)
    result_store = ResultStore(settings.results_data_path)

    # Store in app state for access from routes
    app.state.corpus = corpus
    app.state.cif_parser = cif_parser
    app.state.mileage = mileage
    app.state.darwin = darwin
    app.state.orchestrator = orchestrator
    app.state.audit = audit
    app.state.result_store = result_store
    app.state.settings = settings

    # --- Routes ---
    app.include_router(cif.router)
    app.include_router(timetable.router)
    app.include_router(electric.router)
    app.include_router(health.router)

    return app


app = create_app()
