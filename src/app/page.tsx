'use client';

import { useState, FormEvent, useRef, useEffect } from 'react';
import './globals.css';
import {
  ValidateParams,
  GenerateParams,
  GenerationResult,
  ValidationResult,
  CIFStatus,
  validateInputs,
  startGenerate,
  getGenerateStatus,
  getCIFStatus,
  uploadCIF,
  getTimetableDownloadUrl,
  getRouteDownloadUrl,
  getDebugDownloadUrl,
} from '@/lib/api';

export default function Home() {
  const [form, setForm] = useState<ValidateParams>({
    station_name: '',
    operator_code: '',
    date_start: '',
    date_end: '',
  });
  const [cifFile, setCifFile] = useState<File | null>(null);

  const [cifStatus, setCifStatus] = useState<CIFStatus | null>(null);
  const [cifUploading, setCifUploading] = useState(false);
  const [cifUploadError, setCifUploadError] = useState<string | null>(null);

  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);

  // Fetch CIF status once on mount
  useEffect(() => {
    getCIFStatus().then(setCifStatus).catch(() => {});
  }, []);

  const updateField = (field: keyof ValidateParams, value: string) => {
    setForm(prev => ({ ...prev, [field]: value }));
    setValidation(null);
    setError(null);
  };

  const handleValidate = async () => {
    setValidating(true);
    setError(null);
    try {
      const res = await validateInputs(form);
      setValidation(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Validation failed');
    } finally {
      setValidating(false);
    }
  };

  const handleCIFUpload = async (file: File) => {
    setCifUploading(true);
    setCifUploadError(null);
    try {
      const status = await uploadCIF(file);
      setCifStatus(status);
      setCifFile(null);
    } catch (e) {
      setCifUploadError(e instanceof Error ? e.message : 'CIF upload failed');
    } finally {
      setCifUploading(false);
    }
  };

  const handleGenerate = async (e: FormEvent) => {
    e.preventDefault();
    const serverCifLoaded = cifStatus?.loaded ?? false;
    if (!cifFile && !serverCifLoaded) {
      setError('Please select a CIF timetable file, or upload one to the server first.');
      return;
    }
    setLoading(true);
    setProgress('Uploading CIF file…');
    setError(null);
    setResult(null);

    try {
      const params: GenerateParams = {
        ...form,
        date_end: form.date_end || undefined,
        ...(cifFile ? { cif_file: cifFile } : {}),
      };

      // POST the file; backend returns immediately with a job_id
      const { job_id } = await startGenerate(params);
      setProgress('Processing CIF data — this may take several minutes for large files…');

      // Poll the status endpoint until done or error
      while (true) {
        await new Promise(r => setTimeout(r, 3000));
        const status = await getGenerateStatus(job_id);

        if (status.status === 'done') {
          // The done payload has the same shape as GenerationResult
          setResult(status as unknown as GenerationResult);
          break;
        }

        if (status.status === 'error') {
          throw new Error(status.detail || 'Generation failed');
        }
        // still processing — keep polling
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Generation failed');
    } finally {
      setLoading(false);
      setProgress(null);
    }
  };

  const getFieldError = (field: string): string | undefined => {
    if (!validation || validation.valid) return undefined;
    return validation.errors.find(e => e.field === field)?.message;
  };

  return (
    <div className="container">
      <h1>UK Rail Timetable Generator</h1>
      <p className="subtitle">
        Generate timetable and route CSV outputs from official Network Rail CIF, CORPUS,
        NESA mileage, and Darwin data sources.
      </p>

      {/* CIF Status Bar */}
      <div className="card" style={{ padding: '1rem 1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            {cifStatus?.loaded ? (
              <span style={{ color: 'var(--success, #16a34a)', fontWeight: 600 }}>
                ✓ Server CIF loaded: {cifStatus.filename} — {cifStatus.schedule_count.toLocaleString()} schedules
              </span>
            ) : (
              <span style={{ color: 'var(--warning, #b45309)', fontWeight: 600 }}>
                ⚠ No server CIF loaded — upload one below or include it per request
              </span>
            )}
          </div>
          <label
            style={{
              cursor: cifUploading ? 'not-allowed' : 'pointer',
              background: 'var(--btn-secondary-bg, #e5e7eb)',
              padding: '0.4rem 0.9rem',
              borderRadius: '0.375rem',
              fontSize: '0.875rem',
              fontWeight: 500,
              opacity: cifUploading ? 0.6 : 1,
            }}
          >
            {cifUploading ? 'Uploading…' : cifStatus?.loaded ? 'Replace CIF' : 'Upload CIF to Server'}
            <input
              type="file"
              accept=".CIF,.cif,.MCA,.mca"
              style={{ display: 'none' }}
              disabled={cifUploading}
              onChange={e => {
                const f = e.target.files?.[0];
                if (f) handleCIFUpload(f);
                e.target.value = '';
              }}
            />
          </label>
        </div>
        {cifUploadError && (
          <div className="error-box" style={{ marginTop: '0.5rem' }}>{cifUploadError}</div>
        )}
      </div>

      {/* Input Form */}
      <div className="card">
        <h2>Input Parameters</h2>
        <form onSubmit={handleGenerate}>
          <div className="form-grid">

            {/* CIF file input — shown only when no server CIF is loaded */}
            {!cifStatus?.loaded && (
            <div className="form-group" style={{ gridColumn: '1 / -1' }}>
              <label htmlFor="cif_file">CIF Timetable File</label>
              <input
                id="cif_file"
                type="file"
                accept=".CIF,.cif,.MCA,.mca"
                onChange={e => {
                  setCifFile(e.target.files?.[0] ?? null);
                  setValidation(null);
                  setError(null);
                }}
              />
              <span className="hint">
                Network Rail CIF/MCA timetable file (e.g. toc-full.CIF). Download from
                Rail Data Marketplace or Network Rail Datafeeds.
              </span>
            </div>
            )}

            <div className="form-group">
              <label htmlFor="station_name">Station</label>
              <input
                id="station_name"
                type="text"
                placeholder="e.g. EPS, EPSM, Epsom"
                value={form.station_name}
                onChange={e => updateField('station_name', e.target.value)}
                required
                maxLength={100}
              />
              <span className="hint">CRS code (3 letters), TIPLOC, or station name</span>
              {getFieldError('station_name') && (
                <span className="error-text">{getFieldError('station_name')}</span>
              )}
            </div>

            <div className="form-group">
              <label htmlFor="operator_code">Operator Code (ATOC)</label>
              <input
                id="operator_code"
                type="text"
                placeholder="e.g. SN, VT, GX"
                value={form.operator_code}
                onChange={e => updateField('operator_code', e.target.value.toUpperCase())}
                required
                maxLength={3}
              />
              <span className="hint">2–3 letter TOC code (SN=Southern, GX=Gatwick Express)</span>
              {getFieldError('operator_code') && (
                <span className="error-text">{getFieldError('operator_code')}</span>
              )}
            </div>

            <div className="form-group">
              <label htmlFor="date_start">Date Start</label>
              <input
                id="date_start"
                type="date"
                value={form.date_start}
                onChange={e => updateField('date_start', e.target.value)}
                required
              />
              <span className="hint">Single date or start of range</span>
              {getFieldError('date') && (
                <span className="error-text">{getFieldError('date')}</span>
              )}
            </div>

            <div className="form-group">
              <label htmlFor="date_end">Date End (optional)</label>
              <input
                id="date_end"
                type="date"
                value={form.date_end}
                onChange={e => updateField('date_end', e.target.value)}
              />
              <span className="hint">Leave empty for a single date. Max 31-day range.</span>
              {getFieldError('date_range') && (
                <span className="error-text">{getFieldError('date_range')}</span>
              )}
            </div>

          </div>

          <div className="button-row">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleValidate}
              disabled={validating || loading}
            >
              {validating && <span className="loading-spinner" />}
              Validate Inputs
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || validating || (!cifFile && !cifStatus?.loaded)}
            >
              {loading && <span className="loading-spinner" />}
              {loading ? 'Generating…' : 'Generate CSV'}
            </button>
          </div>
        </form>

        {/* Validation result */}
        {validation && (
          <div style={{ marginTop: '1rem' }}>
            {validation.valid ? (
              <div className="success-box">All inputs are valid.</div>
            ) : (
              <div className="error-box">
                Validation errors:
                <ul style={{ marginTop: '0.25rem', paddingLeft: '1.25rem' }}>
                  {validation.errors.map((e, i) => (
                    <li key={i}><strong>{e.field}</strong>: {e.message}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Progress display */}
      {progress && (
        <div className="success-box" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span className="loading-spinner" />
          {progress}
        </div>
      )}

      {/* Error display */}
      {error && (
        <div className="error-box">{error}</div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Download Buttons */}
          <div className="card">
            <h2>Download Results</h2>
            <div className="button-row">
              <a
                className="btn btn-download"
                href={getTimetableDownloadUrl(result.generation_id)}
                download="timetable.csv"
              >
                Download Timetable CSV ({result.timetable_rows} rows)
              </a>
              <a
                className="btn btn-download"
                href={getRouteDownloadUrl(result.generation_id)}
                download="route.csv"
              >
                Download Route CSV ({result.route_rows} rows)
              </a>
              <a
                className="btn btn-secondary"
                href={getDebugDownloadUrl(result.generation_id)}
                download="debug.csv"
              >
                Download Debug CSV
              </a>
            </div>
          </div>

          {/* Warnings */}
          {result.warnings.length > 0 && (
            <div className="card">
              <h2>Warnings</h2>
              {result.warnings.map((w, i) => (
                <div key={i} className="warning-box">{w}</div>
              ))}
            </div>
          )}

          {/* Summary Statistics */}
          <div className="card">
            <h2>Summary Report</h2>
            <div className="summary-grid">
              <div className="summary-stat">
                <div className="value">{result.summary.total_services_processed}</div>
                <div className="label">Services Processed</div>
              </div>
              <div className="summary-stat">
                <div className="value">{result.summary.timetable_rows_generated}</div>
                <div className="label">Timetable Rows</div>
              </div>
              <div className="summary-stat">
                <div className="value">{result.summary.unique_route_patterns}</div>
                <div className="label">Unique Routes</div>
              </div>
              <div className="summary-stat">
                <div className="value">{result.summary.darwin_enrichment_success_pct}%</div>
                <div className="label">Darwin Enrichment</div>
              </div>
              <div className="summary-stat">
                <div className="value">{result.summary.missing_mileage_count}</div>
                <div className="label">Missing Mileage</div>
              </div>
              <div className="summary-stat">
                <div className="value">{result.summary.dates_in_range}</div>
                <div className="label">Dates in Range</div>
              </div>
            </div>
          </div>

          {/* Source Provenance */}
          <div className="card">
            <h2>Source Provenance</h2>
            <div className="provenance-grid">
              <div className="provenance-item">
                <div className="label">CIF Dataset</div>
                <div>{result.provenance.cif.source}</div>
                <div>Schedules: {result.provenance.cif.total_schedules}</div>
                <div>Extract: {result.provenance.cif.header_date || 'N/A'}</div>
              </div>
              <div className="provenance-item">
                <div className="label">CORPUS Dataset</div>
                <div>{result.provenance.corpus.source}</div>
                <div>TIPLOCs: {result.provenance.corpus.tiploc_count}</div>
                <div>CRS codes: {result.provenance.corpus.crs_count}</div>
              </div>
              <div className="provenance-item">
                <div className="label">Mileage Dataset</div>
                <div>{result.provenance.mileage.source}</div>
                <div>Segments: {result.provenance.mileage.segment_pairs}</div>
                <div>Loaded: {result.provenance.mileage.loaded ? 'Yes' : 'No'}</div>
              </div>
              <div className="provenance-item">
                <div className="label">Darwin Enrichment</div>
                <div>Attempts: {result.summary.darwin_attempts}</div>
                <div>Successes: {result.summary.darwin_successes}</div>
                <div>Success rate: {result.summary.darwin_enrichment_success_pct}%</div>
              </div>
            </div>
          </div>

          {/* Timetable Preview */}
          {result.timetable_preview.length > 0 && (
            <div className="card">
              <h2>Timetable Preview (first 20 rows)</h2>
              <div className="table-wrapper">
                <table>
                  <thead>
                    <tr>
                      <th>Route</th>
                      <th>Stop Type</th>
                      <th>Date</th>
                      <th>Departure</th>
                      <th>Class</th>
                      <th>Coaches</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.timetable_preview.map((row, i) => (
                      <tr key={i}>
                        <td>{row.route_variant}</td>
                        <td>{row.stop_type}</td>
                        <td>{row.date}</td>
                        <td>{row.departure_time}</td>
                        <td>{row.train_class || <span style={{color:'var(--text-muted)'}}>N/A</span>}</td>
                        <td>{row.number_of_coaches || <span style={{color:'var(--text-muted)'}}>N/A</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Route Preview */}
          {result.route_preview.length > 0 && (
            <div className="card">
              <h2>Route Preview (first 20 rows)</h2>
              <div className="table-wrapper">
                <table>
                  <thead>
                    <tr>
                      <th>Variant</th>
                      <th>Seq</th>
                      <th>From</th>
                      <th>To</th>
                      <th>Stop Type</th>
                      <th>Miles</th>
                      <th>Run (min)</th>
                      <th>Wait (min)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.route_preview.map((row, i) => (
                      <tr key={i}>
                        <td>{row.route_variant}</td>
                        <td>{row.seq}</td>
                        <td>{row.from_station}</td>
                        <td>{row.to_station}</td>
                        <td>{row.stop_type}</td>
                        <td>{row.distance_miles || <span style={{color:'var(--text-muted)'}}>N/A</span>}</td>
                        <td>{row.run_min || <span style={{color:'var(--text-muted)'}}>N/A</span>}</td>
                        <td>{row.wait_min}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
