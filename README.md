# UK Rail Timetable Generator - Frontend

Next.js frontend for the UK Rail Timetable Generator. Provides a web interface for generating timetable and route CSVs from official Network Rail data sources.

## Features

- Input form for station, operator, dates, route names
- Input validation with server-side checks
- CSV generation with download buttons
- Results preview (timetable + route tables)
- Source provenance display (CIF, CORPUS, mileage, Darwin)
- Summary statistics and diagnostics
- Warning display for missing data

## Setup

```bash
# Install dependencies
npm install

# Configure backend URL
cp .env.example .env.local
# Edit .env.local if backend is not on localhost:8000

# Run development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Requirements

- Node.js 18+
- Backend API running (see Train-Timetable-Scrapping-Tool/backend)

## Architecture

- Next.js 14 with App Router
- TypeScript
- API client in `src/lib/api.ts`
- Single-page app in `src/app/page.tsx`
- API requests proxied to backend via Next.js rewrites
