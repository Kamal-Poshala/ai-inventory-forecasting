import { useMemo, useState } from 'react'
import type { ItemRisk, RiskStatus } from '../api'
import { STATUS_META, shortDate, usd } from '../format'
import { Card } from './Card'
import { StatusBadge } from './StatusBadge'

const FILTERS: (RiskStatus | 'all')[] = ['all', 'stockout_risk', 'reorder', 'overstock', 'healthy']

export function ItemsTable({ items, selected, onSelect }: { items: ItemRisk[]; selected: string | null; onSelect: (sku: string) => void }) {
  const [filter, setFilter] = useState<RiskStatus | 'all'>('all')
  const [query, setQuery] = useState('')

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    return items.filter(
      (i) => (filter === 'all' || i.status === filter) && (!q || `${i.sku} ${i.name} ${i.category}`.toLowerCase().includes(q)),
    )
  }, [items, filter, query])

  return (
    <Card
      title="Items"
      action={
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search SKU, name, category"
          className="w-56 rounded-md border border-slate-200 px-2.5 py-1 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
        />
      }
    >
      <div className="mb-3 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => {
          const count = f === 'all' ? items.length : items.filter((i) => i.status === f).length
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                filter === f ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              {f === 'all' ? 'All' : STATUS_META[f].label} <span className="opacity-60">{count}</span>
            </button>
          )
        })}
      </div>
      <div className="-mx-5 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr className="border-b border-slate-100">
              <th className="px-5 py-2 font-medium">Item</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 text-right font-medium">On hand</th>
              <th className="px-3 py-2 text-right font-medium">Days cover</th>
              <th className="px-3 py-2 font-medium">Stockout</th>
              <th className="px-3 py-2 text-right font-medium">Trend</th>
              <th className="px-5 py-2 text-right font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => (
              <tr
                key={i.sku}
                onClick={() => onSelect(i.sku)}
                className={`cursor-pointer border-b border-slate-50 transition hover:bg-slate-50 ${selected === i.sku ? 'bg-indigo-50/60' : ''}`}
              >
                <td className="px-5 py-2.5">
                  <div className="font-medium text-slate-900">{i.name}</div>
                  <div className="text-xs text-slate-500">
                    {i.sku} · {i.category}
                  </div>
                </td>
                <td className="px-3 py-2.5">
                  <StatusBadge status={i.status} />
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">{i.on_hand.toLocaleString()}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{i.days_of_cover ?? '—'}</td>
                <td className="px-3 py-2.5 tabular-nums text-slate-600">{i.projected_stockout_date ? shortDate(i.projected_stockout_date) : '—'}</td>
                <td className={`px-3 py-2.5 text-right tabular-nums ${i.trend_pct >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
                  {i.trend_pct >= 0 ? '▲' : '▼'} {Math.abs(i.trend_pct)}%
                </td>
                <td className="px-5 py-2.5 text-right text-xs text-slate-700">
                  {i.recommended_order_qty > 0 ? `Order ${i.recommended_order_qty.toLocaleString()}` : i.excess_value > 0 ? `${usd(i.excess_value)} excess` : '—'}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-5 py-8 text-center text-slate-500">
                  No items match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
