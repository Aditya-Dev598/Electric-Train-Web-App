/**
 * API client for the Rail Timetable Generator backend.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

export interface ValidationError {
  field: string;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: ValidationError[];
}

export interface TimetablePreview {
  date: string;
  departure_time: string;
  route_variant: string;
  train_class: string;
  number_of_coaches: string;
}

export interface RoutePreview {
  route_variant: string;
  seq: number;
  from_station: string;
  to_station: string;
  distance_miles: string;
  run_min: string;
  wait_min: string;
}

export interface GenerationSummary {
  total_services_processed: number;
  timetable_rows_generated: number;
  unique_route_patterns: number;
  darwin_enrichment_success_pct: number;
  darwin_attempts: number;
  darwin_successes: number;
  missing_mileage_count: number;
  dates_in_range: number;
  departures_found: number;
}

export interface Provenance {
  cif: { source: string; path: string; header_date: string; total_schedules: number };
  corpus: { source: string; path: string; loaded: boolean; tiploc_count: number; crs_count: number };
  mileage: { source: string; path: string; loaded: boolean; segment_pairs: number };
}

export interface GenerationResult {
  generation_id: string;
  timetable_rows: number;
  route_rows: number;
  warnings: string[];
  provenance: Provenance;
  summary: GenerationSummary;
  timetable_preview: TimetablePreview[];
  route_preview: RoutePreview[];
}

export interface JobStatusResponse {
  status: 'processing' | 'done' | 'error';
  detail?: string;
  // Populated when status === 'done' (same shape as GenerationResult)
  generation_id?: string;
  timetable_rows?: number;
  route_rows?: number;
  warnings?: string[];
  provenance?: Provenance;
  summary?: GenerationSummary;
  timetable_preview?: TimetablePreview[];
  route_preview?: RoutePreview[];
}

export interface ValidateParams {
  station_name: string;
  operator_code: string;
  date_start: string;
  date_end?: string;
}

export interface GenerateParams extends ValidateParams {
  cif_file: File;
}

export async function validateInputs(params: ValidateParams): Promise<ValidationResult> {
  const resp = await fetch(`${API_BASE}/api/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      station_name: params.station_name,
      operator_code: params.operator_code,
      date_start: params.date_start,
      date_end: params.date_end || undefined,
    }),
  });

  if (resp.status === 422) {
    return await resp.json();
  }

  if (!resp.ok) {
    throw new Error(`Validation request failed: ${resp.status}`);
  }

  return await resp.json();
}

/** POST the CIF file + params; returns a job_id immediately (HTTP 202). */
export async function startGenerate(params: GenerateParams): Promise<{ job_id: string }> {
  const body = new FormData();
  body.append('cif_file', params.cif_file);
  body.append('station_name', params.station_name);
  body.append('operator_code', params.operator_code);
  body.append('date_start', params.date_start);
  if (params.date_end) {
    body.append('date_end', params.date_end);
  }

  const resp = await fetch(`${API_BASE}/api/generate`, {
    method: 'POST',
    body,
  });

  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(detail.detail || `Generation failed: ${resp.status}`);
  }

  return await resp.json();
}

/** Poll the status of an async generation job. */
export async function getGenerateStatus(jobId: string): Promise<JobStatusResponse> {
  const resp = await fetch(`${API_BASE}/api/generate/status/${jobId}`);
  if (!resp.ok) {
    throw new Error(`Status check failed: ${resp.status}`);
  }
  return await resp.json();
}

export function getTimetableDownloadUrl(generationId: string): string {
  return `${API_BASE}/api/download/${generationId}/timetable.csv`;
}

export function getRouteDownloadUrl(generationId: string): string {
  return `${API_BASE}/api/download/${generationId}/route.csv`;
}

export function getDebugDownloadUrl(generationId: string): string {
  return `${API_BASE}/api/download/${generationId}/debug.csv`;
}
