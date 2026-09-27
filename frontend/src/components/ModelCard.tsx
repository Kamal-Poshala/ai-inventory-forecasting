import type { ModelMetrics } from '../api'
import { pct } from '../format'
import { Card } from './Card'

export function ModelCard({ metrics }: { metrics: ModelMetrics }) {
  const m = metrics.metrics
  const bars = [
    { label: 'Gradient boosting (ours)', wape: m.model.wape, color: 'bg-indigo-500' },
    { label: 'Seasonal naive', wape: m.baselines.seasonal_naive_wape, color: 'bg-slate-300' },
    { label: '28-day moving avg', wape: m.baselines.moving_average_28d_wape, color: 'bg-slate-300' },
  ]
  const max = Math.max(...bars.map((b) => b.wape))
  const lift = 1 - m.model.wape / m.baselines.seasonal_naive_wape

  return (
    <Card title="Forecast accuracy" action={<span className="text-xs text-slate-500">{m.horizon_days}-day holdout from {m.holdout_start}</span>}>
      <p className="mb-3 text-xs text-slate-500">Weighted absolute percentage error (WAPE); lower is better.</p>
      <div className="space-y-2.5">
        {bars.map((b) => (
          <div key={b.label}>
            <div className="mb-1 flex justify-between text-xs">
              <span className="text-slate-700">{b.label}</span>
              <span className="font-medium tabular-nums">{pct(b.wape)}</span>
            </div>
            <div className="h-2 rounded-full bg-slate-100">
              <div className={`h-2 rounded-full ${b.color}`} style={{ width: `${(b.wape / max) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
      <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-slate-100 pt-3 text-center">
        <div>
          <dt className="text-[11px] text-slate-500">vs seasonal naive</dt>
          <dd className="font-semibold text-emerald-600">{lift >= 0 ? '−' : '+'}{pct(Math.abs(lift), 0)} error</dd>
        </div>
        <div>
          <dt className="text-[11px] text-slate-500">Bias</dt>
          <dd className="font-semibold tabular-nums">{m.model.bias >= 0 ? '+' : ''}{pct(m.model.bias)}</dd>
        </div>
        <div>
          <dt className="text-[11px] text-slate-500">p10–p90 coverage</dt>
          <dd className="font-semibold tabular-nums" title={`Raw quantile band; production band widened ×${m.interval_scale} to reach 80%`}>
            {pct(m.model.interval_coverage_p10_p90, 0)} → 80%
          </dd>
        </div>
      </dl>
    </Card>
  )
}
