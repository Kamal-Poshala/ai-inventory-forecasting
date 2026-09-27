import type { Summary } from '../api'
import { usd } from '../format'

export function KpiCards({ summary }: { summary: Summary }) {
  const kpis = [
    { label: 'Stockout risk', value: summary.counts.stockout_risk, hint: 'run out before restock arrives', accent: 'text-red-600' },
    { label: 'Below reorder point', value: summary.counts.reorder, hint: `${usd(summary.reorder_value)} to replenish`, accent: 'text-amber-600' },
    { label: 'Overstocked', value: summary.counts.overstock, hint: `${usd(summary.excess_value)} excess inventory`, accent: 'text-violet-600' },
    { label: 'Inventory value', value: usd(summary.inventory_value), hint: `${summary.total_items} SKUs tracked`, accent: 'text-slate-900' },
  ]
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {kpis.map((k) => (
        <div key={k.label} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{k.label}</p>
          <p className={`mt-1 text-2xl font-semibold tabular-nums ${k.accent}`}>{k.value}</p>
          <p className="mt-0.5 text-xs text-slate-500">{k.hint}</p>
        </div>
      ))}
    </div>
  )
}
