'use client';

import { useState, FormEvent, useEffect } from 'react';
import './globals.css';
import CsvEditor from '@/components/CsvEditor';
import {
  ValidateParams,
  GenerateParams,
  GenerationResult,
  ValidationResult,
  CIFStatus,
  StoredResult,
  ElectricStatus,
  ElectricRunResult,
  SolarRunResult,
  validateInputs,
  startGenerate,
  getGenerateStatus,
  getCIFStatus,
  uploadCIF,
  listResults,
  deleteResult,
  getElectricStatus,
  uploadElectricFile,
  runElectricPipeline,
  runSolarPipeline,
  getTimetableDownloadUrl,
  getRouteDownloadUrl,
  getDebugDownloadUrl,
  getElectricOutputUrl,
  getSolarOutputUrl,
} from '@/lib/api';

export default function Home() {
  // ── Form state ────────────────────────────────────────────────────────────
  const [form, setForm] = useState<ValidateParams>({
    station_name: '',
    operator_code: '',
    date_start: '',
    date_end: '',
  });
  const [cifFile, setCifFile] = useState<File | null>(null);

  // ── CIF status ────────────────────────────────────────────────────────────
  const [cifStatus, setCifStatus] = useState<CIFStatus | null>(null);
  const [cifUploading, setCifUploading] = useState(false);
  const [cifUploadError, setCifUploadError] = useState<string | null>(null);

  // ── Generation ────────────────────────────────────────────────────────────
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [validating, setValidating] = useState(false);

  // ── Results history (Phase 2) ─────────────────────────────────────────────
  const [storedResults, setStoredResults] = useState<StoredResult[]>([]);
  const [selectedResultIds, setSelectedResultIds] = useState<Set<string>>(new Set());
  const [activeEditorResult, setActiveEditorResult] = useState<{ id: string; type: 'timetable' | 'route' } | null>(null);

  // ── Solar pipeline (Phase 5) ──────────────────────────────────────────────
  const [solarDemandFile, setSolarDemandFile] = useState<File | null>(null);
  const [solarPvgisFile, setSolarPvgisFile] = useState<File | null>(null);
  const [solarRunning, setSolarRunning] = useState(false);
  const [solarResult, setSolarResult] = useState<SolarRunResult | null>(null);
  const [solarError, setSolarError] = useState<string | null>(null);

  // ── Electric pipeline (Phase 4) ───────────────────────────────────────────
  const [electricStatus, setElectricStatus] = useState<ElectricStatus | null>(null);
  const [electricUploading, setElectricUploading] = useState<Record<string, boolean>>({});
  const [electricRunning, setElectricRunning] = useState(false);
  const [electricResult, setElectricResult] = useState<ElectricRunResult | null>(null);
  const [electricError, setElectricError] = useState<string | null>(null);

  // ── Init ──────────────────────────────────────────────────────────────────
  useEffect(() => {
    getCIFStatus().then(setCifStatus).catch(() => {});
    loadStoredResults();
    getElectricStatus().then(setElectricStatus).catch(() => {});
  }, []);

  const loadStoredResults = () => {
    listResults().then(setStoredResults).catch(() => {});
  };

  // ── Helpers ───────────────────────────────────────────────────────────────
  const updateField = (field: keyof ValidateParams, value: string) => {
    setForm(prev => ({ ...prev, [field]: value }));
    setValidation(null);
    setError(null);
  };

  const getFieldError = (field: string): string | undefined => {
    if (!validation || validation.valid) return undefined;
    return validation.errors.find(e => e.field === field)?.message;
  };

  // ── CIF upload ────────────────────────────────────────────────────────────
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

  // ── Validation ────────────────────────────────────────────────────────────
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

  // ── Generation ────────────────────────────────────────────────────────────
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
    setActiveEditorResult(null);

    try {
      const params: GenerateParams = {
        ...form,
        date_end: form.date_end || undefined,
        ...(cifFile ? { cif_file: cifFile } : {}),
      };

      const { job_id } = await startGenerate(params);
      setProgress('Processing CIF data — this may take several minutes for large files…');

      while (true) {
        await new Promise(r => setTimeout(r, 3000));
        const status = await getGenerateStatus(job_id);

        if (status.status === 'done') {
          setResult(status as unknown as GenerationResult);
          loadStoredResults();
          break;
        }
        if (status.status === 'error') {
          throw new Error(status.detail || 'Generation failed');
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Generation failed');
    } finally {
      setLoading(false);
      setProgress(null);
    }
  };

  // ── Results history ───────────────────────────────────────────────────────
  const handleDeleteResult = async (id: string) => {
    await deleteResult(id).catch(() => {});
    setStoredResults(prev => prev.filter(r => r.generation_id !== id));
    setSelectedResultIds(prev => { const s = new Set(prev); s.delete(id); return s; });
    if (activeEditorResult?.id === id) setActiveEditorResult(null);
    if (result?.generation_id === id) setResult(null);
  };

  const toggleResultSelection = (id: string) => {
    setSelectedResultIds(prev => {
      const s = new Set(prev);
      s.has(id) ? s.delete(id) : s.add(id);
      return s;
    });
  };

  // ── Electric pipeline ─────────────────────────────────────────────────────
  const handleElectricUpload = async (fileType: 'rolling_stock' | 'station_points' | 'tss_points', file: File) => {
    setElectricUploading(prev => ({ ...prev, [fileType]: true }));
    setElectricError(null);
    try {
      await uploadElectricFile(fileType, file);
      const status = await getElectricStatus();
      setElectricStatus(status);
    } catch (e) {
      setElectricError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setElectricUploading(prev => ({ ...prev, [fileType]: false }));
    }
  };

  const handleRunSolar = async () => {
    if (!solarDemandFile || !solarPvgisFile) {
      setSolarError('Please select both the demand CSV and the PVGIS CSV files.');
      return;
    }
    setSolarRunning(true);
    setSolarError(null);
    setSolarResult(null);
    try {
      const res = await runSolarPipeline(solarDemandFile, solarPvgisFile);
      setSolarResult(res);
    } catch (e) {
      setSolarError(e instanceof Error ? e.message : 'Solar pipeline failed');
    } finally {
      setSolarRunning(false);
    }
  };

  const handleRunElectric = async () => {
    if (selectedResultIds.size === 0) {
      setElectricError('Select at least one result from the history table above.');
      return;
    }
    setElectricRunning(true);
    setElectricError(null);
    setElectricResult(null);
    try {
      const res = await runElectricPipeline(Array.from(selectedResultIds));
      setElectricResult(res);
    } catch (e) {
      setElectricError(e instanceof Error ? e.message : 'Electric pipeline failed');
    } finally {
      setElectricRunning(false);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="container">
      <h1>UK Rail Timetable Generator</h1>
      <p className="subtitle">
        Generate timetable and route CSV outputs from official Network Rail CIF, CORPUS,
        NESA mileage, and Darwin data sources.
      </p>

      {/* ── CIF Status Bar ──────────────────────────────────────────────── */}
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
          <label style={{
            cursor: cifUploading ? 'not-allowed' : 'pointer',
            background: 'var(--btn-secondary-bg, #e5e7eb)',
            padding: '0.4rem 0.9rem',
            borderRadius: '0.375rem',
            fontSize: '0.875rem',
            fontWeight: 500,
            opacity: cifUploading ? 0.6 : 1,
          }}>
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
        {cifUploadError && <div className="error-box" style={{ marginTop: '0.5rem' }}>{cifUploadError}</div>}
      </div>

      {/* ── Input Form ──────────────────────────────────────────────────── */}
      <div className="card">
        <h2>Input Parameters</h2>
        <form onSubmit={handleGenerate}>
          <div className="form-grid">

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

      {progress && (
        <div className="success-box" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span className="loading-spinner" />
          {progress}
        </div>
      )}

      {error && <div className="error-box">{error}</div>}

      {/* ── Latest Generation Result ──────────────────────────────────── */}
      {result && (
        <>
          <div className="card">
            <h2>Download Results</h2>
            <div className="button-row">
              <a className="btn btn-download" href={getTimetableDownloadUrl(result.generation_id)} download="timetable.csv">
                Download Timetable CSV ({result.timetable_rows} rows)
              </a>
              <a className="btn btn-download" href={getRouteDownloadUrl(result.generation_id)} download="route.csv">
                Download Route CSV ({result.route_rows} rows)
              </a>
              <a className="btn btn-secondary" href={getDebugDownloadUrl(result.generation_id)} download="debug.csv">
                Download Debug CSV
              </a>
            </div>
          </div>

          {result.warnings.length > 0 && (
            <div className="card">
              <h2>Warnings</h2>
              {result.warnings.map((w, i) => (
                <div key={i} className="warning-box">{w}</div>
              ))}
            </div>
          )}

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

          {/* Timetable editor */}
          <div className="card">
            <h2>Timetable Editor</h2>
            <p className="hint" style={{ marginBottom: '0.75rem' }}>
              Edit cells directly, delete rows, then click Save Changes. Fill in <code>train_type</code> and <code>cars</code> columns before running the Electric pipeline.
            </p>
            <CsvEditor resultId={result.generation_id} csvType="timetable" />
          </div>

          {/* Route editor */}
          <div className="card">
            <h2>Route Editor</h2>
            <CsvEditor resultId={result.generation_id} csvType="route" />
          </div>
        </>
      )}

      {/* ── Results History ────────────────────────────────────────────── */}
      <div className="card">
        <h2>Results History</h2>
        {storedResults.length === 0 ? (
          <p className="hint">No saved results yet. Generate a timetable to see it here.</p>
        ) : (
          <>
            <p className="hint" style={{ marginBottom: '0.5rem' }}>
              Check rows to select them for the Electric pipeline. Click a row to open the in-browser editor.
            </p>
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 32 }}></th>
                    <th>Station</th>
                    <th>Operator</th>
                    <th>Date Start</th>
                    <th>Date End</th>
                    <th>Rows</th>
                    <th>Generated</th>
                    <th>Edit</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {storedResults.map(r => (
                    <tr key={r.generation_id} style={{ background: selectedResultIds.has(r.generation_id) ? 'var(--highlight-bg, #eff6ff)' : undefined }}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selectedResultIds.has(r.generation_id)}
                          onChange={() => toggleResultSelection(r.generation_id)}
                        />
                      </td>
                      <td>{r.station_name}</td>
                      <td>{r.operator_code}</td>
                      <td>{r.date_start}</td>
                      <td>{r.date_end}</td>
                      <td>{r.timetable_rows}</td>
                      <td style={{ fontSize: '0.8rem' }}>{new Date(r.generated_at).toLocaleString()}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          style={{ padding: '0.2rem 0.6rem', fontSize: '0.78rem' }}
                          onClick={() => setActiveEditorResult(
                            activeEditorResult?.id === r.generation_id ? null : { id: r.generation_id, type: 'timetable' }
                          )}
                        >
                          {activeEditorResult?.id === r.generation_id ? 'Close' : 'Open'}
                        </button>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          style={{ padding: '0.2rem 0.6rem', fontSize: '0.78rem', color: 'var(--error, #dc2626)' }}
                          onClick={() => handleDeleteResult(r.generation_id)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {/* Inline editor for a history result */}
      {activeEditorResult && (
        <div className="card">
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem', alignItems: 'center' }}>
            <h2 style={{ margin: 0 }}>
              Editing: {storedResults.find(r => r.generation_id === activeEditorResult.id)?.station_name} — {activeEditorResult.type}
            </h2>
            {(['timetable', 'route'] as const).map(t => (
              <button
                key={t}
                type="button"
                className={`btn ${activeEditorResult.type === t ? 'btn-primary' : 'btn-secondary'}`}
                style={{ padding: '0.25rem 0.75rem', fontSize: '0.82rem' }}
                onClick={() => setActiveEditorResult(prev => prev ? { ...prev, type: t } : null)}
              >
                {t}
              </button>
            ))}
          </div>
          <CsvEditor resultId={activeEditorResult.id} csvType={activeEditorResult.type} />
        </div>
      )}

      {/* ── Electric Pipeline ──────────────────────────────────────────── */}
      <div className="card">
        <h2>Electric Train Pipeline</h2>
        <p className="hint" style={{ marginBottom: '1rem' }}>
          Upload reference files, select results above, then run the energy analysis.
          Make sure to fill in <code>train_type</code> and <code>cars</code> columns in the editor before running.
        </p>

        {/* Reference file uploads */}
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          {([
            { key: 'rolling_stock', label: 'Rolling Stock CSV' },
            { key: 'station_points', label: 'Station Points CSV' },
            { key: 'tss_points', label: 'TSS Points CSV (optional)' },
          ] as const).map(({ key, label }) => (
            <div key={key} style={{ flex: '1 1 180px' }}>
              <div style={{ fontSize: '0.85rem', fontWeight: 500, marginBottom: '0.3rem' }}>
                {electricStatus?.[key] ? (
                  <span style={{ color: 'var(--success, #16a34a)' }}>✓ </span>
                ) : (
                  <span style={{ color: 'var(--text-muted, #6b7280)' }}>○ </span>
                )}
                {label}
              </div>
              <label style={{
                cursor: electricUploading[key] ? 'not-allowed' : 'pointer',
                background: 'var(--btn-secondary-bg, #e5e7eb)',
                padding: '0.3rem 0.7rem',
                borderRadius: '0.375rem',
                fontSize: '0.82rem',
                display: 'inline-block',
                opacity: electricUploading[key] ? 0.6 : 1,
              }}>
                {electricUploading[key] ? 'Uploading…' : electricStatus?.[key] ? 'Replace' : 'Upload'}
                <input
                  type="file"
                  accept=".csv"
                  style={{ display: 'none' }}
                  disabled={!!electricUploading[key]}
                  onChange={e => {
                    const f = e.target.files?.[0];
                    if (f) handleElectricUpload(key, f);
                    e.target.value = '';
                  }}
                />
              </label>
            </div>
          ))}
        </div>

        <div className="button-row">
          <button
            type="button"
            className="btn btn-primary"
            disabled={electricRunning || !electricStatus?.rolling_stock || !electricStatus?.station_points || selectedResultIds.size === 0}
            onClick={handleRunElectric}
          >
            {electricRunning && <span className="loading-spinner" />}
            {electricRunning ? 'Running…' : `Run Electric Pipeline (${selectedResultIds.size} result${selectedResultIds.size !== 1 ? 's' : ''} selected)`}
          </button>
        </div>

        {electricError && <div className="error-box" style={{ marginTop: '0.75rem' }}>{electricError}</div>}

        {electricResult && (
          <div style={{ marginTop: '1rem' }}>
            <div className="success-box">Pipeline complete — {electricResult.tss_files.length} TSS output file{electricResult.tss_files.length !== 1 ? 's' : ''}</div>
            <div className="button-row" style={{ marginTop: '0.5rem', flexWrap: 'wrap' }}>
              {electricResult.tss_files.map(f => (
                <a
                  key={f}
                  className="btn btn-download"
                  href={getElectricOutputUrl(electricResult.run_id, f)}
                  download={f}
                  style={{ fontSize: '0.82rem' }}
                >
                  {f}
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
      {/* ── Solar Pipeline ────────────────────────────────────────────── */}
      <div className="card">
        <h2>Solar Analysis Pipeline</h2>
        <p className="hint" style={{ marginBottom: '1rem' }}>
          Upload a half-hour TSS demand CSV (output from the Electric pipeline above) and a
          PVGIS hourly solar radiation CSV. The pipeline produces demand/supply profiles,
          an average-day plot, and annual + seasonal solar metrics.
        </p>

        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          <div className="form-group" style={{ flex: '1 1 220px', margin: 0 }}>
            <label style={{ fontWeight: 500, fontSize: '0.875rem' }}>
              Half-hour Demand CSV
              {solarDemandFile && <span style={{ color: 'var(--success, #16a34a)', marginLeft: '0.5rem' }}>✓ {solarDemandFile.name}</span>}
            </label>
            <input
              type="file"
              accept=".csv"
              onChange={e => { setSolarDemandFile(e.target.files?.[0] ?? null); setSolarError(null); }}
              style={{ marginTop: '0.25rem' }}
            />
            <span className="hint">Output from Electric pipeline (per-TSS file with 48 half-hour columns)</span>
          </div>

          <div className="form-group" style={{ flex: '1 1 220px', margin: 0 }}>
            <label style={{ fontWeight: 500, fontSize: '0.875rem' }}>
              PVGIS Solar CSV
              {solarPvgisFile && <span style={{ color: 'var(--success, #16a34a)', marginLeft: '0.5rem' }}>✓ {solarPvgisFile.name}</span>}
            </label>
            <input
              type="file"
              accept=".csv"
              onChange={e => { setSolarPvgisFile(e.target.files?.[0] ?? null); setSolarError(null); }}
              style={{ marginTop: '0.25rem' }}
            />
            <span className="hint">Download from PVGIS (hourly radiation, format: time,P,...)</span>
          </div>
        </div>

        <div className="button-row">
          <button
            type="button"
            className="btn btn-primary"
            disabled={solarRunning || !solarDemandFile || !solarPvgisFile}
            onClick={handleRunSolar}
          >
            {solarRunning && <span className="loading-spinner" />}
            {solarRunning ? 'Analysing…' : 'Run Solar Analysis'}
          </button>
        </div>

        {solarError && <div className="error-box" style={{ marginTop: '0.75rem' }}>{solarError}</div>}

        {solarResult && (
          <div style={{ marginTop: '1rem' }}>
            <div className="success-box" style={{ marginBottom: '0.75rem' }}>
              Analysis complete — Solar share: <strong>{solarResult.solar_share_pct.toFixed(1)}%</strong> &nbsp;|&nbsp;
              Utilisation: <strong>{solarResult.utilisation_pct.toFixed(1)}%</strong>
            </div>

            {/* Inline plot */}
            <div style={{ marginBottom: '1rem', textAlign: 'center' }}>
              <img
                src={`data:image/png;base64,${solarResult.avg_profile_png_b64}`}
                alt="Average demand/supply profile"
                style={{ maxWidth: '100%', borderRadius: '0.5rem', border: '1px solid var(--border, #e5e7eb)' }}
              />
            </div>

            {/* Download links */}
            <div className="button-row" style={{ flexWrap: 'wrap' }}>
              {solarResult.files.map(f => (
                <a
                  key={f}
                  className="btn btn-download"
                  href={getSolarOutputUrl(solarResult.run_id, f)}
                  download={f}
                  style={{ fontSize: '0.82rem' }}
                >
                  {f}
                </a>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
