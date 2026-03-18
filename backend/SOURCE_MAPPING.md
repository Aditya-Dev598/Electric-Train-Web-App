# Source-to-Field Mapping Table

## Timetable CSV

| CSV Field | Source | Record/Field | Logic |
|-----------|--------|-------------|-------|
| `date` | CIF | BS: date_runs_from/to + days_run + STP overlay | Date from user range, validated against schedule |
| `departure_time` | CIF | LO: public_departure / LI: public_departure | Public time preferred, falls back to WTT scheduled |
| `train_route` | User | Form input | Pass-through (sanitized) |
| `train_class` | Darwin | Formation → classCode / category | Queried by Train UID + date; empty if unavailable |
| `number_of_coaches` | Darwin | Formation → coach count / trainLength | Queried by Train UID + date; empty if unavailable |

## Route CSV

| CSV Field | Source | Record/Field | Logic |
|-----------|--------|-------------|-------|
| `route_variant` | User | Form input | Pass-through (sanitized) |
| `seq` | Derived | CIF location sequence | Sequential 1..n from origin |
| `from_station` | CIF + CORPUS | LO/LI TIPLOC → CORPUS CRS | Only passenger stations with CRS |
| `to_station` | CIF + CORPUS | LI/LT TIPLOC → CORPUS CRS | Next passenger calling point |
| `distance_miles` | NESA | Official mileage data | TIPLOC pair lookup; empty if not found |
| `run_min` | CIF | departure_A → arrival_B | Minutes with midnight handling |
| `wait_min` | CIF | arrival → departure at same station | 0 for origin/pass-through |

## CIF Record Layout

| Record | Columns Used | Fields Extracted |
|--------|-------------|-----------------|
| BS | 3-8, 9-14, 15-20, 21-27, 79 | UID, dates, days_run, STP |
| BX | 11-12 | ATOC code |
| LO | 2-9, 10-14, 15-18 | TIPLOC, departure times |
| LI | 2-9, 10-14, 15-19, 25-28, 29-32 | TIPLOC, arr/dep times |
| LT | 2-9, 10-14, 15-18 | TIPLOC, arrival time |
