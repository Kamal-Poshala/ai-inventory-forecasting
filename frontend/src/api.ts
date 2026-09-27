export type RiskStatus = 'stockout_risk' | 'reorder' | 'overstock' | 'healthy'

export interface ItemRisk {
  sku: string
  name: string
  category: string
  on_hand: number
  unit_cost: number
  lead_time_days: number
  avg_daily_forecast: number
  avg_daily_last_28: number
  trend_pct: number
  days_of_cover: number | null
  projected_stockout_date: string | null
  reorder_point: number
  safety_stock: number
  recommended_order_qty: number
  excess_units: number
  excess_value: number
  status: RiskStatus
}

export interface ItemDetail {
  risk: ItemRisk
  history: { date: string; qty: number }[]
  forecast: { date: string; p10: number; p50: number; p90: number }[]
  forecast_wape: number | null
}

export interface Summary {
  total_items: number
  counts: Record<RiskStatus, number>
  inventory_value: number
  excess_value: number
  reorder_value: number
  last_trained_at: string | null
}

export interface ModelMetrics {
  run_id: number
  trained_at: string
  metrics: {
    horizon_days: number
    holdout_start: string
    interval_scale: number
    model: { wape: number; mae: number; bias: number; interval_coverage_p10_p90: number }
    baselines: { seasonal_naive_wape: number; moving_average_28d_wape: number }
  }
}

export interface Briefing {
  headline: string
  summary: string
  key_risks: { sku: string; issue: string; recommendation: string }[]
  trends: string[]
  actions: { priority: 'high' | 'medium' | 'low'; action: string; rationale: string }[]
  source: 'llm' | 'rules'
  model: string | null
  generated_at: string | null
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatReply {
  answer: string
  tool_calls: { name: string; input: Record<string, unknown>; is_error: boolean }[]
  model: string
}

export interface UploadResult {
  rows: number
  inserted: number
  updated: number
  warnings: string[]
  retrained: boolean
  run_id: number | null
}

export class ApiError extends Error {
  status: number
  details: string[]

  constructor(status: number, message: string, details: string[] = []) {
    super(message)
    this.status = status
    this.details = details
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = body.detail
    if (detail && typeof detail === 'object' && 'errors' in detail) throw new ApiError(res.status, detail.message, detail.errors)
    if (Array.isArray(detail)) throw new ApiError(res.status, detail.map((d: { msg: string }) => d.msg).join('; '))
    throw new ApiError(res.status, detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

const upload = (file: File): RequestInit => {
  const form = new FormData()
  form.append('file', file)
  return { method: 'POST', body: form }
}

export const api = {
  summary: () => request<Summary>('/summary'),
  items: () => request<ItemRisk[]>('/items'),
  item: (sku: string) => request<ItemDetail>(`/items/${encodeURIComponent(sku)}?history_days=120`),
  metrics: () => request<ModelMetrics>('/model/metrics'),
  insights: (refresh = false) => request<Briefing>(`/insights${refresh ? '?refresh=true' : ''}`),
  retrain: () => request<ModelMetrics>('/model/retrain', { method: 'POST' }),
  health: () => request<{ status: string; llm_enabled: boolean }>('/health'),
  updateItem: (sku: string, patch: Partial<Pick<ItemRisk, 'on_hand' | 'lead_time_days' | 'unit_cost'>>) =>
    request<ItemRisk>(`/items/${encodeURIComponent(sku)}`, json('PATCH', patch)),
  chat: (messages: ChatMessage[]) => request<ChatReply>('/chat', json('POST', { messages })),
  uploadItems: (file: File) => request<UploadResult>('/data/items', upload(file)),
  uploadSales: (file: File, mode: 'append' | 'replace') => request<UploadResult>(`/data/sales?mode=${mode}`, upload(file)),
}
