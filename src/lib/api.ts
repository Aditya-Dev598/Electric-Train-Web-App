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
  stop_type: string;
  train_class: string;
  number_of_coaches: string;
}

export interface RoutePreview {
  route_variant: string;
  seq: number;
  from_station: string;
  to_station: string;
  stop_type: string;
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
  cif_file?: File;
}

export interface CIFStatus {
  loaded: boolean;
  loading: boolean;
  filename: string | null;
  schedule_count: number;
  loaded_at: string | null;
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

export async function getCIFStatus(): Promise<CIFStatus> {
  const resp = await fetch(`${API_BASE}/api/cif/status`);
  if (!resp.ok) throw new Error(`CIF status check failed: ${resp.status}`);
  return await resp.json();
}

export async function uploadCIF(file: File): Promise<{ loading: boolean; filename: string }> {
  const body = new FormData();
  body.append('cif_file', file);
  // Returns 202 immediately — backend parses in background
  const resp = await fetch(`${API_BASE}/api/cif/upload`, { method: 'POST', body });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(detail.detail || `CIF upload failed: ${resp.status}`);
  }
  return await resp.json();
}

/** POST the CIF file + params; returns a job_id immediately (HTTP 202). */
export async function startGenerate(params: GenerateParams): Promise<{ job_id: string }> {
  const body = new FormData();
  if (params.cif_file) body.append('cif_file', params.cif_file);
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

// ---------------------------------------------------------------------------
// Results history (Phase 2)
// ---------------------------------------------------------------------------

export interface StoredResult {
  generation_id: string;
  station_name: string;
  operator_code: string;
  date_start: string;
  date_end: string;
  generated_at: string;
  timetable_rows: number;
  route_rows: number;
}

export async function listResults(): Promise<StoredResult[]> {
  const resp = await fetch(`${API_BASE}/api/results`);
  if (!resp.ok) throw new Error(`Failed to list results: ${resp.status}`);
  return await resp.json();
}

export async function deleteResult(generationId: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/results/${generationId}`, { method: 'DELETE' });
  if (!resp.ok) throw new Error(`Delete failed: ${resp.status}`);
}

// ---------------------------------------------------------------------------
// Electric pipeline (Phase 4)
// ---------------------------------------------------------------------------

export interface ElectricStatus {
  rolling_stock: boolean;
  station_points: boolean;
  timetable: boolean;
  route: boolean;
}

export async function getElectricStatus(): Promise<ElectricStatus> {
  const resp = await fetch(`${API_BASE}/api/electric/status`);
  if (!resp.ok) throw new Error(`Electric status failed: ${resp.status}`);
  return await resp.json();
}

export async function uploadElectricFile(
  fileType: 'rolling_stock' | 'station_points' | 'timetable' | 'route',
  file: File,
): Promise<void> {
  const body = new FormData();
  body.append('file', file);
  const resp = await fetch(`${API_BASE}/api/electric/upload/${fileType}`, { method: 'POST', body });
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(d.detail || `Upload failed: ${resp.status}`);
  }
}

export interface ElectricRunResult {
  run_id: string;
  tss_files: string[];
  created_at?: string;
}

export interface ElectricRunMeta {
  run_id: string;
  tss_files: string[];
  created_at: string;
}

export async function listElectricRuns(): Promise<ElectricRunMeta[]> {
  const resp = await fetch(`${API_BASE}/api/electric/runs`);
  if (!resp.ok) throw new Error(`Failed to list electric runs: ${resp.status}`);
  return await resp.json();
}

export async function runElectricPipeline(resultIds: string[] = []): Promise<{ job_id: string }> {
  const resp = await fetch(`${API_BASE}/api/electric/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ result_ids: resultIds }),
  });
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(d.detail || `Electric run failed: ${resp.status}`);
  }
  return await resp.json();
}

export interface ElectricJobStatus {
  status: 'processing' | 'done' | 'error';
  run_id?: string;
  tss_files?: string[];
  created_at?: string;
  error?: string;
}

export async function getElectricJobStatus(jobId: string): Promise<ElectricJobStatus> {
  const resp = await fetch(`${API_BASE}/api/electric/job/${jobId}`);
  if (!resp.ok) {
    throw new Error(`Electric job status check failed: ${resp.status}`);
  }
  return await resp.json();
}

export function getElectricOutputUrl(runId: string, tssName: string): string {
  return `${API_BASE}/api/electric/output/${runId}/${tssName}`;
}

export interface MergeResult {
  gen_id: string;
  timetable_rows: number;
  route_rows: number;
}

export async function mergeResults(resultIds: string[]): Promise<MergeResult> {
  const resp = await fetch(`${API_BASE}/api/electric/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ result_ids: resultIds }),
  });
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(d.detail || `Merge failed: ${resp.status}`);
  }
  return await resp.json();
}

// ---------------------------------------------------------------------------
// Solar pipeline (Phase 5)
// ---------------------------------------------------------------------------

export interface SolarRunResult {
  run_id: string;
  solar_share_pct: number;
  utilisation_pct: number;
  files: string[];
  avg_profile_png_b64: string;
  created_at?: string;
}

export interface SolarRunMeta {
  run_id: string;
  solar_share_pct: number;
  utilisation_pct: number;
  files: string[];
  avg_profile_png_b64: string;
  created_at: string;
}

export async function listSolarRuns(): Promise<SolarRunMeta[]> {
  const resp = await fetch(`${API_BASE}/api/solar/runs`);
  if (!resp.ok) throw new Error(`Failed to list solar runs: ${resp.status}`);
  return await resp.json();
}

export async function runSolarPipeline(demandCsv: File, pvgisCsv: File): Promise<SolarRunResult> {
  const body = new FormData();
  body.append('demand_csv', demandCsv);
  body.append('pvgis_csv', pvgisCsv);
  const resp = await fetch(`${API_BASE}/api/solar/run`, { method: 'POST', body });
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(d.detail || `Solar run failed: ${resp.status}`);
  }
  return await resp.json();
}

export function getSolarOutputUrl(runId: string, filename: string): string {
  return `${API_BASE}/api/solar/output/${runId}/${filename}`;
}
