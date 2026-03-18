# CIF ↔ Darwin Matching Strategy

## Overview

The matching strategy connects CIF schedule records to Darwin real-time data
to enrich timetable output with `train_class` and `number_of_coaches`.

## Matching Algorithm

### Primary: UID-based matching

1. Extract **Train UID** (6 chars) from CIF BS record (columns 3-8)
2. Query Darwin OpenLDBWS for departures at the target station
3. Match Darwin service `uid` field against CIF Train UID

### Confidence Scoring

| Level | Criteria | Action |
|-------|----------|--------|
| **EXACT** | UID matches + origin departure time exact match | Extract formation data |
| **HIGH** | UID matches + time within ±2 minutes | Extract formation data |
| **MEDIUM** | UID matches + time differs > 2 min | Log warning, extract cautiously |
| **LOW** | No UID match, headcode + time match (±5 min) | Log warning, extract if available |
| **FAILED** | No match found | Return empty fields |

### Fallback: Headcode matching

If UID matching fails:
1. Use **train identity** (headcode, 4 chars) from CIF BS record (columns 32-35)
2. Match against Darwin `trainid` field
3. Validate by checking departure time within ±5 minutes
4. Assign LOW confidence

## Logged Fields (Always)

For every matching attempt, the following are logged:

- `cif_uid` - The CIF Train UID
- `stp_indicator` - P/O/C/N from CIF
- `service_date` - The specific operating date
- `darwin_rid` - Darwin RID (if matched)
- `matching_method` - How the match was made
- `confidence` - EXACT/HIGH/MEDIUM/LOW/FAILED
- `train_class` - Extracted value or empty
- `number_of_coaches` - Extracted value or empty
- `failure_reason` - Why matching failed (if applicable)

## Darwin Data Extraction

Once a match is confirmed (EXACT or HIGH confidence):

1. Query `GetServiceDetails` with the Darwin RID
2. Parse XML response for:
   - `<formation>` → count `<coach>` elements → `number_of_coaches`
   - `<coach classCode="X">` → `train_class`
   - `<trainLength>` → fallback for coach count
   - `<category>` → fallback for train class

## Edge Cases

| Scenario | Handling |
|----------|----------|
| Darwin unavailable | Return empty, log, warn |
| No formation data | Return empty coaches, log |
| Retimed service | UID match still works, time tolerance applied |
| Split/join service | Use primary service RID |
| Bus replacement | Exclude from enrichment |
| Historical date | Darwin may not have data, return empty |
