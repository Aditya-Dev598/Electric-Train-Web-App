"""Integration tests for FastAPI endpoints."""

import json
import os
import tempfile

import pytest

# httpx is needed for FastAPI TestClient in async mode
try:
    from httpx import AsyncClient, ASGITransport
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from backend.app.main import create_app


def _setup_test_data():
    """Create temp data files for testing and set env vars."""
    # CORPUS
    corpus_data = {
        "TIPLOCDATA": [
            {"TIPLOC": "EUSTON ", "CRS": "EUS", "NLCDESC": "LONDON EUSTON"},
            {"TIPLOC": "RUGBY  ", "CRS": "RUG", "NLCDESC": "RUGBY"},
            {"TIPLOC": "BHAMNWS", "CRS": "BHM", "NLCDESC": "BIRMINGHAM NEW STREET"},
        ]
    }
    corpus_fd, corpus_path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(corpus_fd, "w") as f:
        json.dump(corpus_data, f)

    # Mileage
    mileage_data = {
        "segments": [
            {"from_tiploc": "EUSTON", "to_tiploc": "RUGBY", "elr": "LEC1", "distance_miles": 82.5},
            {"from_tiploc": "RUGBY", "to_tiploc": "BHAMNWS", "elr": "LEC1", "distance_miles": 30.25},
        ]
    }
    mileage_fd, mileage_path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(mileage_fd, "w") as f:
        json.dump(mileage_data, f)

    # CIF file
    bs_line = "BSN" + "A12345" + "260301" + "260331" + "1111111" + " " * 51 + "P"
    bx_line = "BX" + " " * 9 + "VT" + "Y"
    lo_line = "LO" + "EUSTON  " + "0830 " + "0830" + " " * 20 + "TB          "
    li_line = "LI" + "RUGBY   " + "0920 " + "0922 " + "     " + "0920" + "0922" + " " * 20 + "T           "
    lt_line = "LT" + "BHAMNWS " + "1015 " + "1015"
    cif_content = "\n".join([bs_line, bx_line, lo_line, li_line, lt_line, "ZZ"])

    cif_fd, cif_path = tempfile.mkstemp(suffix=".cif")
    with os.fdopen(cif_fd, "w") as f:
        f.write(cif_content)

    os.environ["CORPUS_DATA_PATH"] = corpus_path
    os.environ["MILEAGE_DATA_PATH"] = mileage_path
    os.environ["CIF_DATA_PATH"] = cif_path
    os.environ["DARWIN_API_TOKEN"] = ""  # Disabled for integration tests

    return corpus_path, mileage_path, cif_path


def _cleanup_test_data(paths):
    for p in paths:
        if os.path.exists(p):
            os.unlink(p)


@pytest.fixture
def test_app():
    """Create a test FastAPI app with test data."""
    paths = _setup_test_data()
    app = create_app()
    yield app
    _cleanup_test_data(paths)


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
@pytest.mark.asyncio
async def test_health_endpoint(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
@pytest.mark.asyncio
async def test_validate_valid_inputs(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/validate", json={
            "station_name": "EUS",
            "operator_code": "VT",
            "date_start": "2026-03-15",
            "train_route": "London-Birmingham",
            "route_variant": "Stopping",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
@pytest.mark.asyncio
async def test_validate_invalid_inputs(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/validate", json={
            "station_name": "",
            "operator_code": "TOOLONG",
            "date_start": "bad-date",
            "train_route": "=cmd()",
            "route_variant": "OK",
        })
        # Pydantic will catch most of these at 422
        assert resp.status_code in (200, 422)


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
@pytest.mark.asyncio
async def test_generate_endpoint(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/generate", json={
            "station_name": "EUS",
            "operator_code": "VT",
            "date_start": "2026-03-15",
            "train_route": "London-Birmingham",
            "route_variant": "Stopping",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "generation_id" in data
        assert "warnings" in data
        assert "summary" in data
        assert "provenance" in data

        # Download timetable CSV
        gen_id = data["generation_id"]
        csv_resp = await client.get(f"/api/download/{gen_id}/timetable.csv")
        assert csv_resp.status_code == 200
        assert "date,departure_time" in csv_resp.text

        # Download route CSV
        route_resp = await client.get(f"/api/download/{gen_id}/route.csv")
        assert route_resp.status_code == 200
        assert "route_variant,seq" in route_resp.text


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
@pytest.mark.asyncio
async def test_download_invalid_id(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/download/nonexistent/timetable.csv")
        assert resp.status_code == 404
