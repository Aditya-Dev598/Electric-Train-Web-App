# UK Rail Timetable Generator - Backend

Python FastAPI backend for generating railway timetable and route CSV outputs from official UK rail data sources.

## Data Sources

- **Network Rail CIF** — timetable schedules, STP overlays
- **Network Rail CORPUS** — station/CRS/TIPLOC mapping
- **Network Rail NESA** — official rail mileage
- **Darwin OpenLDBWS** — train class + coach count enrichment

## Quick Start

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure (copy and edit .env)
cp .env.example .env
# Edit .env with your data paths and Darwin API token

# Place data files:
# - CIF data in data/cif/ (*.mca or *.cif files)
# - CORPUS JSON at data/corpus/CORPUSExtract.json
# - Mileage JSON at data/mileage/mileage.json

# Run server
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/validate` | Validate user inputs |
| POST | `/api/generate` | Generate timetable + route CSVs |
| GET | `/api/download/{id}/timetable.csv` | Download timetable CSV |
| GET | `/api/download/{id}/route.csv` | Download route CSV |
| GET | `/api/download/{id}/debug.csv` | Download debug/diagnostic CSV |
| GET | `/api/health` | Health check |
| GET | `/api/docs` | Swagger UI |

## Data Preparation

### CIF Data
Download from [Network Rail Data Feeds](https://datafeeds.networkrail.co.uk/) or [Rail Data Marketplace](https://raildata.org.uk/). Place `.mca` or `.cif` files in the `CIF_DATA_PATH` directory.

### CORPUS Data
Download the CORPUS extract JSON from Network Rail Data Feeds. Expected format:
```json
{"TIPLOCDATA": [{"TIPLOC": "KNGX", "CRS": "KGX", "NLCDESC": "LONDON KINGS CROSS", ...}]}
```

### Mileage Data
Download from NESA or Rail Data Marketplace. Convert to JSON format:
```json
{"segments": [{"from_tiploc": "EUSTON", "to_tiploc": "RUGBY", "elr": "LEC1", "distance_miles": 82.5}]}
```

### Darwin API
Register at [National Rail Enquiries](https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/) for an API token.

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest backend/tests/ -v
```

## Project Structure

```
backend/
├── app/
│   ├── main.py              # FastAPI application factory
│   ├── config.py             # Settings from environment
│   ├── models.py             # Data models
│   ├── routers/
│   │   ├── timetable.py      # Generation endpoints
│   │   └── health.py         # Health check
│   ├── services/
│   │   ├── orchestrator.py   # Pipeline coordinator
│   │   ├── cif_parser.py     # CIF fixed-width parser
│   │   ├── corpus_mapper.py  # CORPUS station resolver
│   │   ├── mileage_resolver.py # NESA distance lookups
│   │   ├── darwin_enricher.py  # Darwin API integration
│   │   ├── schedule_validator.py # STP overlay logic
│   │   ├── route_builder.py  # Route pattern extraction
│   │   ├── csv_exporter.py   # CSV generation
│   │   └── audit_logger.py   # Structured logging
│   ├── security/
│   │   ├── validators.py     # Input validation
│   │   └── middleware.py      # Rate limiting, headers
│   └── utils/
│       └── time_utils.py     # Time parsing/calculation
├── tests/
│   ├── unit/                 # Unit tests
│   └── integration/          # API integration tests
├── data/sample/              # Sample CSV outputs
├── requirements.txt          # Production dependencies
├── requirements-dev.txt      # Dev/test dependencies
└── .env.example             # Environment template
```

## Documentation

- [Architecture Summary](ARCHITECTURE.md)
- [Source-to-Field Mapping](SOURCE_MAPPING.md)
- [CIF-Darwin Matching Strategy](CIF_DARWIN_MATCHING.md)
- [Security Report](SECURITY_REPORT.md)
