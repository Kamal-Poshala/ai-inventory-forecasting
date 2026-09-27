import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { api, ApiError, type UploadResult } from '../api'

function UploadSection({
  title,
  description,
  template,
  onUpload,
  children,
}: {
  title: string
  description: string
  template: string
  onUpload: (file: File) => Promise<UploadResult>
  children?: ReactNode
}) {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const mutation = useMutation({ mutationFn: onUpload, onSuccess: () => qc.invalidateQueries() })
  const error = mutation.error instanceof ApiError ? mutation.error : null

  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="text-xs text-slate-500">{description}</p>
        </div>
        <a href={`/api/data/templates/${template}.csv`} className="shrink-0 text-xs font-medium text-indigo-600 hover:underline">
          Template
        </a>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null)
            mutation.reset()
          }}
          className="text-xs file:mr-2 file:rounded-md file:border-0 file:bg-slate-100 file:px-2.5 file:py-1.5 file:text-xs file:font-medium hover:file:bg-slate-200"
        />
        {children}
        <button
          onClick={() => file && mutation.mutate(file)}
          disabled={!file || mutation.isPending}
          className="ml-auto rounded-md bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {mutation.isPending ? 'Uploading…' : 'Upload'}
        </button>
      </div>
      {mutation.data && (
        <div className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          {mutation.data.rows} rows: {mutation.data.inserted} added, {mutation.data.updated} updated
          {mutation.data.retrained && ' · model retrained'}
          {mutation.data.warnings.map((w) => (
            <p key={w} className="mt-1 text-amber-700">⚠ {w}</p>
          ))}
        </div>
      )}
      {mutation.error && (
        <div className="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">
          <p className="font-medium">{mutation.error.message}</p>
          {error?.details.length ? (
            <ul className="mt-1 list-disc pl-4">
              {error.details.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </div>
  )
}

export function DataDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<'append' | 'replace'>('append')
  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Import data">
        <header className="flex items-center justify-between border-b border-slate-100 px-5 py-3">
          <h2 className="text-sm font-semibold">Import your data</h2>
          <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close">
            ✕
          </button>
        </header>
        <div className="space-y-4 p-5">
          <UploadSection
            title="1. Items"
            description="sku, name, category, unit_cost, lead_time_days, on_hand. Existing SKUs are updated."
            template="items"
            onUpload={api.uploadItems}
          />
          <UploadSection
            title="2. Daily sales"
            description="sku, date, qty. The model retrains automatically after upload."
            template="sales"
            onUpload={(f) => api.uploadSales(f, mode)}
          >
            <select value={mode} onChange={(e) => setMode(e.target.value as 'append' | 'replace')} className="rounded-md border border-slate-200 px-2 py-1 text-xs">
              <option value="append">Merge with existing</option>
              <option value="replace">Replace all sales</option>
            </select>
          </UploadSection>
          <p className="text-xs text-slate-500">Forecasts need at least ~90 days of daily history per item; a year or more captures seasonality.</p>
        </div>
      </div>
    </div>
  )
}
