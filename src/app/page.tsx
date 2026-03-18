'use client';

import { useState, FormEvent } from 'react';
import './globals.css';
import {
  GenerateParams,
  GenerationResult,
  ValidationResult,
  validateInputs,
  generateCSV,
  getTimetableDownloadUrl,
  getRouteDownloadUrl,
  getDebugDownloadUrl,
} from '@/lib/api';

export default function Home() {
  const [form, setForm] = useState<GenerateParams>({
    station_name: '',
    operator_code: '',
    date_start: '',
    date_end: '',
    train_route: '',
    route_variant: '',
  });

  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [validating, setValidating] = useState(false);

  const updateField = (field: keyof GenerateParams, value: string) => {
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

  const handleGenerate = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const params = { ...form };
      if (!params.date_end) {
        delete params.date_end;
      }
      const res = await generateCSV(params);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Generation failed');
    } finally {
      setLoading(false);
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

      {/* Input Form */}
      <div className="card">
        <h2>Input Parameters</h2>
        <form onSubmit={handleGenerate}>
          <div className="form-grid">
            <div className="form-group">
              <label htmlFor="station_name">Station Name</label>
              <input
                id="station_name"
                type="text"
                placeholder="e.g. KGX, WATRLMN, London Kings Cross"
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
                placeholder="e.g. VT, SWR, GW"
                value={form.operator_code}
                onChange={e => updateField('operator_code', e.target.value.toUpperCase())}
                required
                maxLength={3}
              />
              <span className="hint">2-3 letter TOC/operator code</span>
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
              <span className="hint">Leave empty for single date. Max 31 day range.</span>
              {getFieldError('date_range') && (
                <span className="error-text">{getFieldError('date_range')}</span>
              )}
            </div>

            <div className="form-group">
              <label htmlFor="train_route">Train Route</label>
              <input
                id="train_route"
                type="text"
                placeholder="e.g. London-Edinburgh Main"
                value={form.train_route}
                onChange={e => updateField('train_route', e.target.value)}
                required
                maxLength={200}
              />
              <span className="hint">User-defined route name for CSV output</span>
              {getFieldError('train_route') && (
                <span className="error-text">{getFieldError('train_route')}</span>
              )}
            </div>

            <div className="form-group">
              <label htmlFor="route_variant">Route Variant</label>
              <input
                id="route_variant"
                type="text"
                placeholder="e.g. Stopping, Express, Via-Peterborough"
                value={form.route_variant}
                onChange={e => updateField('route_variant', e.target.value)}
                required
                maxLength={200}
              />
              <span className="hint">User-defined variant label for route CSV</span>
              {getFieldError('route_variant') && (
                <span className="error-text">{getFieldError('route_variant')}</span>
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
              disabled={loading || validating}
            >
              {loading && <span className="loading-spinner" />}
              Generate CSV
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
                      <th>Date</th>
                      <th>Departure Time</th>
                      <th>Train Route</th>
                      <th>Train Class</th>
                      <th>Coaches</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.timetable_preview.map((row, i) => (
                      <tr key={i}>
                        <td>{row.date}</td>
                        <td>{row.departure_time}</td>
                        <td>{row.train_route}</td>
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
