import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Area, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api'
import { pct, shortDate } from '../format'
import { Card } from './Card'
import { StatusBadge } from './StatusBadge'

interface ChartRow {
  date: string
  actual?: number
  p50?: number
  band?: [number, number]
}

function StockEditor({ sku, onHand, onSaved }: { sku: string; onHand: number; onSaved?: () => void }) {
  const qc = useQueryClient()
  const [value, setValue] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (n: number) => api.updateItem(sku, { on_hand: n }),
    onSuccess: () => {
      setValue(null)
      onSaved?.()
      qc.invalidateQueries()
    },
  })

  if (value === null)
    return (
      <button
        onClick={() => setValue(String(onHand))}
        className="font-medium tabular-nums underline decoration-slate-300 decoration-dotted underline-offset-2 hover:text-indigo-600"
        title="Update stock on hand (e.g. after a cycle count)"
      >
        {onHand.toLocaleString()}
      </button>
    )

  const n = Number(value)
  const valid = value.trim() !== '' && Number.isInteger(n) && n >= 0
  return (
    <form
      className="flex items-center gap-1"
      onSubmit={(e) => {
        e.preventDefault()
        if (valid) save.mutate(n)
      }}
    >
      <input
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        inputMode="numeric"
        aria-label="Units on hand"
        className="w-20 rounded border border-slate-300 px-1.5 py-0.5 text-sm tabular-nums"
      />
      <button type="submit" disabled={!valid || save.isPending} className="text-xs font-medium text-indigo-600 disabled:opacity-40">
        Save
      </button>
      <button type="button" onClick={() => setValue(null)} className="text-xs text-slate-400" aria-label="Cancel">
        ✕
      </button>
    </form>
  )
}

export function ItemDetail({ sku, onStockChange }: { sku: string; onStockChange?: () => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['item', sku], queryFn: () => api.item(sku) })

  if (isLoading) return <Card title="Loading…"><div className="h-72 animate-pulse rounded bg-slate-100" /></Card>
  if (error || !data) return <Card title="Error">{String(error)}</Card>

  const { risk, history, forecast } = data
  const last = history[history.length - 1]
  const rows: ChartRow[] = [
    ...history.map((h) => ({ date: h.date, actual: h.qty })),
    // Anchor the forecast to the last actual so the lines connect.
    ...(last ? [{ date: last.date, actual: last.qty, p50: last.qty, band: [last.qty, last.qty] as [number, number] }] : []),
    ...forecast.map((f) => ({ date: f.date, p50: +f.p50.toFixed(1), band: [+f.p10.toFixed(1), +f.p90.toFixed(1)] as [number, number] })),
  ].filter((r, i, arr) => i === arr.length - 1 || r.date !== arr[i + 1].date)

  const stats: [string, ReactNode][] = [
    ['On hand', <StockEditor key={risk.on_hand} sku={risk.sku} onHand={risk.on_hand} onSaved={onStockChange} />],
    ['Reorder point', risk.reorder_point.toLocaleString()],
    ['Safety stock', risk.safety_stock.toLocaleString()],
    ['Lead time', `${risk.lead_time_days} days`],
    ['Forecast / day', risk.avg_daily_forecast.toString()],
    ['Last 28d / day', risk.avg_daily_last_28.toString()],
    ['Recommended order', risk.recommended_order_qty ? risk.recommended_order_qty.toLocaleString() : '—'],
    ['Forecast error (WAPE)', data.forecast_wape != null ? pct(data.forecast_wape) : '—'],
  ]

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          {risk.name} <span className="font-normal text-slate-400">{risk.sku}</span>
        </span>
      }
      action={<StatusBadge status={risk.status} />}
    >
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
            <CartesianGrid stroke="#f1f5f9" vertical={false} />
            <XAxis dataKey="date" tickFormatter={shortDate} tick={{ fontSize: 11, fill: '#64748b' }} minTickGap={40} />
            <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
            <Tooltip labelFormatter={(d) => shortDate(String(d))} contentStyle={{ fontSize: 12, borderRadius: 8 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Area dataKey="band" name="p10–p90 range" stroke="none" fill="#6366f1" fillOpacity={0.15} isAnimationActive={false} />
            <Line dataKey="actual" name="Actual units" stroke="#334155" strokeWidth={1.5} dot={false} isAnimationActive={false} />
            <Line dataKey="p50" name="Forecast (p50)" stroke="#6366f1" strokeWidth={2} strokeDasharray="5 3" dot={false} isAnimationActive={false} />
            {last && <ReferenceLine x={last.date} stroke="#94a3b8" strokeDasharray="2 2" label={{ value: 'today', fontSize: 10, fill: '#94a3b8', position: 'insideTopRight' }} />}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
        {stats.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-slate-500">{k}</dt>
            <dd className="font-medium tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {risk.projected_stockout_date && (
        <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          Projected to run out on <strong>{shortDate(risk.projected_stockout_date)}</strong> at forecast demand.
        </p>
      )}
    </Card>
  )
}
