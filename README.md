# UK Rail Timetable Generator

Full-stack web application for generating railway timetable and route CSV outputs from **official UK rail data sources only**.

## Structure

```
Electric-Train-Web-App/
├── backend/              # Python FastAPI API server
│   ├── app/              # Application code
│   ├── tests/            # 133 tests (unit + integration)
│   ├── data/sample/      # Sample CSV outputs
│   ├── requirements.txt
│   └── .env.example
├── src/                  # Next.js frontend
│   ├── app/
│   └── lib/
├── package.json
└── pyproject.toml
```

## Data Sources

| Source | Used For |
|--------|----------|
| **Network Rail CIF** | All timetable/schedule data, STP overlays |
| **Network Rail CORPUS** | Station name ↔ CRS ↔ TIPLOC mapping |
| **Network Rail NESA** | Official rail mileage (miles and chains) |
| **Darwin OpenLDBWS** | train_class and number_of_coaches only |

## Quick Start

### Backend

```bash
# Install Python dependencies
pip install -r backend/requirements.txt

# Configure
cp backend/.env.example backend/.env
# Edit backend/.env with your data paths and Darwin API token

# Place data files:
#   backend/data/cif/          ← CIF/MCA timetable files
#   backend/data/corpus/CORPUSExtract.json
#   backend/data/mileage/mileage.json

# Start API server
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

API docs: `http://localhost:8000/api/docs`

### Frontend

```bash
npm install
npm run dev
```

Open `http://localhost:3000`

## Running Tests

```bash
pip install -r backend/requirements-dev.txt
python -m pytest backend/tests/ -v
```

## Documentation

- [Backend README](backend/README.md)
- [Architecture Summary](backend/ARCHITECTURE.md)
- [Source-to-Field Mapping](backend/SOURCE_MAPPING.md)
- [CIF-Darwin Matching Strategy](backend/CIF_DARWIN_MATCHING.md)
- [Security Report](backend/SECURITY_REPORT.md)
