import type { RiskStatus } from '../api'
import { STATUS_META } from '../format'

export function StatusBadge({ status }: { status: RiskStatus }) {
  const meta = STATUS_META[status]
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${meta.badge}`}>
      <span className={`size-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
    </span>
  )
}
