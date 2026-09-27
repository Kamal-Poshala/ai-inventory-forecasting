import type { RiskStatus } from './api'

export const usd = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })

export const pct = (n: number, digits = 1) => `${(n * 100).toFixed(digits)}%`

export const shortDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

export const STATUS_META: Record<RiskStatus, { label: string; badge: string; dot: string }> = {
  stockout_risk: { label: 'Stockout risk', badge: 'bg-red-50 text-red-700 ring-red-600/20', dot: 'bg-red-500' },
  reorder: { label: 'Reorder', badge: 'bg-amber-50 text-amber-800 ring-amber-600/20', dot: 'bg-amber-500' },
  overstock: { label: 'Overstock', badge: 'bg-violet-50 text-violet-700 ring-violet-600/20', dot: 'bg-violet-500' },
  healthy: { label: 'Healthy', badge: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20', dot: 'bg-emerald-500' },
}
