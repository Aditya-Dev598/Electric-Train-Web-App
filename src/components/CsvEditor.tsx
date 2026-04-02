'use client';

import { useEffect, useState, useCallback } from 'react';
import DataGrid, { Column, SelectColumn, textEditor } from 'react-data-grid';
import 'react-data-grid/lib/styles.css';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

interface CsvEditorProps {
  resultId: string;
  csvType: 'timetable' | 'route';
  /** Called after a successful save so parent can refresh downloads */
  onSaved?: () => void;
}

// _rowId is an internal stable key — never sent to the server
type Row = Record<string, string> & { _rowId: string };

export default function CsvEditor({ resultId, csvType, onSaved }: CsvEditorProps) {
  const [rows, setRows] = useState<Row[]>([]);
  const [columns, setColumns] = useState<Column<Row>[]>([]);
  const [csvKeys, setCsvKeys] = useState<string[]>([]);
  const [selectedRows, setSelectedRows] = useState<ReadonlySet<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [recalcing, setRecalcing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const rowKey = useCallback((row: Row) => row._rowId, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`${API_BASE}/api/results/${resultId}/${csvType}.json`)
      .then(r => {
        if (!r.ok) throw new Error(`Failed to load ${csvType}: ${r.status}`);
        return r.json() as Promise<Record<string, string>[]>;
      })
      .then(data => {
        if (data.length === 0) {
          setRows([]);
          setColumns([]);
          setCsvKeys([]);
          return;
        }
        // Derive column keys from original data BEFORE stamping _rowId
        const keys = Object.keys(data[0]);
        setCsvKeys(keys);
        const cols: Column<Row>[] = [
          SelectColumn,
          ...keys.map(k => ({
            key: k,
            name: k,
            resizable: true,
            editable: true,
            renderEditCell: textEditor,  // required by react-data-grid beta to open editor
            minWidth: 80,
            frozen: k === 'route_variant',
          })),
        ];
        setColumns(cols);
        // Stamp a stable _rowId on each row so editing never invalidates the key
        const stamped: Row[] = data.map(r => ({ ...r, _rowId: crypto.randomUUID() }));
        setRows(stamped);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [resultId, csvType]);

  const handleDeleteSelected = () => {
    setRows(prev => prev.filter(r => !selectedRows.has(rowKey(r))));
    setSelectedRows(new Set());
  };

  const handleAddRow = () => {
    const emptyRow: Row = { _rowId: crypto.randomUUID() };
    csvKeys.forEach(k => { emptyRow[k] = ''; });
    setRows(prev => [...prev, emptyRow]);
  };

  const handleRenumberSeq = () => {
    if (!csvKeys.includes('seq') || !csvKeys.includes('route_variant')) return;

    // Group rows by route_variant, sort each group by current seq, renumber from 1
    const grouped = new Map<string, Row[]>();
    rows.forEach(row => {
      const rv = row['route_variant'] ?? '';
      if (!grouped.has(rv)) grouped.set(rv, []);
      grouped.get(rv)!.push(row);
    });

    const renumbered: Row[] = [];
    grouped.forEach(group => {
      group
        .sort((a, b) => Number(a['seq']) - Number(b['seq']))
        .forEach((row, idx) => {
          renumbered.push({ ...row, seq: String(idx + 1) });
        });
    });

    setRows(renumbered);
  };

  const handleRecalcDistances = async () => {
    setRecalcing(true);
    setError(null);
    try {
      const payload = rows.map(({ _rowId, ...rest }) => rest);
      const resp = await fetch(
        `${API_BASE}/api/results/${resultId}/recalculate-distances`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
      );
      if (!resp.ok) {
        const d = await resp.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(d.detail || `Recalculate failed: ${resp.status}`);
      }
      const { rows: updated } = await resp.json();
      setRows((updated as Record<string, string>[]).map((r, i) => ({
        ...r,
        _rowId: rows[i]?._rowId ?? String(i),
      })));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Recalculate failed');
    } finally {
      setRecalcing(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveMsg(null);
    setError(null);
    try {
      // Strip internal _rowId before sending to server
      const payload = rows.map(({ _rowId, ...rest }) => rest);
      const resp = await fetch(`${API_BASE}/api/results/${resultId}/${csvType}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
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
          className="btn btn-secondary"
          style={{ padding: '0.25rem 0.75rem', fontSize: '0.8rem' }}
          disabled={csvKeys.length === 0}
          onClick={handleAddRow}
        >
          Add Row
        </button>
        {csvType === 'route' && csvKeys.includes('seq') && (
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '0.25rem 0.75rem', fontSize: '0.8rem' }}
            onClick={handleRenumberSeq}
          >
            Renumber Seq
          </button>
        )}
        {csvType === 'route' && (
          <button
            type="button"
            className="btn btn-secondary"
            style={{ padding: '0.25rem 0.75rem', fontSize: '0.8rem' }}
            disabled={recalcing}
            onClick={handleRecalcDistances}
          >
            {recalcing ? 'Recalculating…' : 'Recalc Distances'}
          </button>
        )}
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
      <p style={{ fontSize: '0.75rem', color: 'var(--text-muted, #6b7280)', marginTop: '0.35rem' }}>
        Double-click a cell to edit. Press Enter or Tab to confirm. Click Save Changes to persist.
      </p>
    </div>
  );
}
