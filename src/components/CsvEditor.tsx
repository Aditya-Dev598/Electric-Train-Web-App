'use client';

import { useEffect, useState, useCallback } from 'react';
import DataGrid, { Column, SelectColumn } from 'react-data-grid';
import 'react-data-grid/lib/styles.css';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

interface CsvEditorProps {
  resultId: string;
  csvType: 'timetable' | 'route';
  /** Called after a successful save so parent can refresh downloads */
  onSaved?: () => void;
}

type Row = Record<string, string>;

export default function CsvEditor({ resultId, csvType, onSaved }: CsvEditorProps) {
  const [rows, setRows] = useState<Row[]>([]);
  const [columns, setColumns] = useState<Column<Row>[]>([]);
  const [selectedRows, setSelectedRows] = useState<ReadonlySet<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  // Unique row key: concatenate all values (rows don't have natural IDs)
  const rowKey = useCallback((row: Row) => JSON.stringify(row), []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`${API_BASE}/api/results/${resultId}/${csvType}.json`)
      .then(r => {
        if (!r.ok) throw new Error(`Failed to load ${csvType}: ${r.status}`);
        return r.json() as Promise<Row[]>;
      })
      .then(data => {
        if (data.length === 0) {
          setRows([]);
          setColumns([]);
          return;
        }
        const keys = Object.keys(data[0]);
        const cols: Column<Row>[] = [
          SelectColumn,
          ...keys.map(k => ({
            key: k,
            name: k,
            resizable: true,
            editable: true,
            minWidth: 80,
            frozen: k === 'route_variant',
          })),
        ];
        setColumns(cols);
        setRows(data);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [resultId, csvType]);

  const handleDeleteSelected = () => {
    setRows(prev => prev.filter(r => !selectedRows.has(rowKey(r))));
    setSelectedRows(new Set());
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveMsg(null);
    setError(null);
    try {
      const resp = await fetch(`${API_BASE}/api/results/${resultId}/${csvType}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rows),
      });
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(d.detail || `Save failed: ${resp.status}`);
      }
      const { rows: count } = await resp.json();
      setSaveMsg(`Saved ${count} rows`);
      onSaved?.();
      setTimeout(() => setSaveMsg(null), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="hint">Loading {csvType} data…</div>;
  if (error) return <div className="error-box">{error}</div>;

  return (
    <div>
      {/* Toolbar */}
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
        <span className="hint" style={{ margin: 0 }}>{rows.length} rows</span>
        <button
          type="button"
          className="btn btn-secondary"
          style={{ padding: '0.25rem 0.75rem', fontSize: '0.8rem' }}
          disabled={selectedRows.size === 0}
          onClick={handleDeleteSelected}
        >
          Delete Selected ({selectedRows.size})
        </button>
        <button
          type="button"
          className="btn btn-primary"
          style={{ padding: '0.25rem 0.75rem', fontSize: '0.8rem' }}
          disabled={saving}
          onClick={handleSave}
        >
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
        {saveMsg && <span style={{ color: 'var(--success, #16a34a)', fontSize: '0.85rem' }}>{saveMsg}</span>}
      </div>

      {/* Grid */}
      <DataGrid
        columns={columns}
        rows={rows}
        rowKeyGetter={rowKey}
        selectedRows={selectedRows}
        onSelectedRowsChange={setSelectedRows}
        onRowsChange={setRows}
        style={{ height: 380, fontSize: '0.82rem' }}
        className="rdg-light"
      />
    </div>
  );
}
