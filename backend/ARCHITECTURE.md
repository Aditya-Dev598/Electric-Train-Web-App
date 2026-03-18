# Architecture Summary

## System Overview

The UK Rail Timetable Generator is a web application with a **Python FastAPI backend** and **Next.js frontend**. It generates railway timetable and route CSV outputs using ONLY official UK rail data sources.

## Data Sources (Strict)

| Source | Used For | Access |
|--------|----------|--------|
| **Network Rail CIF** | ALL timetable data, schedule validity, STP overlays | File-based (MCA/CIF) |
| **Network Rail CORPUS** | Station name ↔ CRS ↔ TIPLOC mapping | JSON extract |
| **Network Rail NESA** | Rail distance (miles) between stations | JSON dataset |
| **Darwin OpenLDBWS** | train_class, number_of_coaches ONLY | SOAP API |

## Backend Architecture

```
FastAPI App
├── Middleware: CORS, Rate Limit, Request Size, Security Headers
├── Routers
│   ├── /api/validate    (POST) - Validate user inputs
│   ├── /api/generate    (POST) - Generate CSVs
│   ├── /api/download/{id}/timetable.csv (GET)
│   ├── /api/download/{id}/route.csv     (GET)
│   ├── /api/download/{id}/debug.csv     (GET)
│   └── /api/health      (GET)
└── Services
    ├── Orchestrator     - Coordinates full pipeline
    ├── CIF Parser       - Fixed-width record parsing (BS/BX/LO/LI/LT)
    ├── CORPUS Mapper    - Exact-match station resolution
    ├── Mileage Resolver - Official distance lookups
    ├── Darwin Enricher  - UID-based matching + formation data
    ├── Schedule Validator - Date/STP overlay logic
    ├── Route Builder    - Unique pattern extraction
    ├── CSV Exporter     - Injection-protected output
    └── Audit Logger     - Structured provenance logging
```

## Processing Pipeline

1. **Input Validation** → strict server-side checks, allowlists
2. **CORPUS Lookup** → station name → CRS + TIPLOC (exact match only)
3. **CIF Filtering** → operator code + station TIPLOC + date range
4. **STP Overlay** → apply P/O/C/N priority per date per UID
5. **Darwin Enrichment** → match by UID + date, extract formation data
6. **Route Patterns** → deduplicate calling sequences, build station pairs
7. **Mileage Resolution** → NESA lookup for each station pair
8. **CSV Generation** → timetable + route + debug CSVs with headers

## Security Measures

- Strict input validation with regex patterns
- CSV injection protection (prefix dangerous chars)
- Rate limiting (token bucket)
- Request size limits
- CORS restrictions
- Security headers (CSP, X-Frame-Options, etc.)
- No shell execution from user input
- No sensitive data in logs
- Path traversal protection
- SSRF protection (no user-controlled URLs in backend requests)

## Key Design Decisions

1. **File-based CIF parsing**: CIF data loaded at startup, not per-request
2. **In-memory result cache**: Results stored by UUID for download; production should use Redis
3. **Darwin optional**: If token not configured, enrichment fields are empty (not errors)
4. **Exact match only**: CORPUS lookups use exact matching to prevent ambiguity
5. **STP overlay priority**: C > O > N > P, applied per-date per-UID
