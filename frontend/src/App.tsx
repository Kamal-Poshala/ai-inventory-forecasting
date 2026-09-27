import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from './api'
import { ChatPanel } from './components/ChatPanel'
import { DataDialog } from './components/DataDialog'
import { InsightsPanel } from './components/InsightsPanel'
import { ItemDetail } from './components/ItemDetail'
import { ItemsTable } from './components/ItemsTable'
import { KpiCards } from './components/KpiCards'
import { ModelCard } from './components/ModelCard'

export default function App() {
  const qc = useQueryClient()
  const summary = useQuery({ queryKey: ['summary'], queryFn: api.summary })
  const items = useQuery({ queryKey: ['items'], queryFn: api.items })
  const metrics = useQuery({ queryKey: ['metrics'], queryFn: api.metrics })
  const [selected, setSelected] = useState<string | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [dataOpen, setDataOpen] = useState(false)

  const current = selected ?? items.data?.[0]?.sku ?? null

  const retrain = useMutation({ mutationFn: api.retrain, onSuccess: () => qc.invalidateQueries() })

  const select = (sku: string) => {
    setSelected(sku)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const fetchError = summary.error ?? items.error ?? metrics.error
  const loadError = fetchError?.message === 'Failed to fetch' ? 'Could not reach the API. Is the backend running on port 8000?' : (fetchError ?? retrain.error)?.message

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">
              StockSense <span className="font-normal text-slate-400">· AI inventory forecasting</span>
            </h1>
            <p className="text-xs text-slate-500">
              {summary.data?.last_trained_at ? `Model trained ${new Date(summary.data.last_trained_at + 'Z').toLocaleString()}` : 'Demand forecasts, stock risk, and AI-generated briefings'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => setDataOpen(true)} className="rounded-lg border border-slate-200 px-3.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              Import data
            </button>
            <button
              onClick={() => retrain.mutate()}
              disabled={retrain.isPending}
              className="rounded-lg bg-slate-900 px-3.5 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-700 disabled:opacity-60"
            >
              {retrain.isPending ? 'Retraining…' : 'Retrain model'}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
        {loadError ? (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            {loadError}
          </div>
        ) : null}

        {summary.data && <KpiCards summary={summary.data} />}

        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            {current && (
              // Pin the item so it stays in view even if re-sorting moves it off the top of the list.
              <ItemDetail sku={current} onStockChange={() => setSelected(current)} />
            )}
            {items.data && <ItemsTable items={items.data} selected={current} onSelect={select} />}
          </div>
          <div className="space-y-6">
            <InsightsPanel onSelect={select} />
            {metrics.data && <ModelCard metrics={metrics.data} />}
          </div>
        </div>
      </main>

      <button
        onClick={() => setChatOpen(true)}
        className="fixed bottom-6 right-6 z-20 flex items-center gap-2 rounded-full bg-indigo-600 px-5 py-3 text-sm font-medium text-white shadow-lg shadow-indigo-600/30 hover:bg-indigo-700"
      >
        <span>✦</span> Ask your inventory
      </button>
      <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
      <DataDialog open={dataOpen} onClose={() => setDataOpen(false)} />
    </div>
  )
}
