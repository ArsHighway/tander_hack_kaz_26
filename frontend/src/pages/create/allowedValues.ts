// Диапазон характеристики позиции: подпись под полем мастера (`formModel.fieldHelper`).

export type AllowedRange = { min?: number | null; max?: number | null }

/** Диапазон с хотя бы одной границей. Перепутанные границы и пустой объект — не диапазон. */
export function allowedRange(range: AllowedRange | null | undefined): { min: number | null; max: number | null } | null {
  if (!range || typeof range !== 'object') return null
  const min = typeof range.min === 'number' && Number.isFinite(range.min) ? range.min : null
  const max = typeof range.max === 'number' && Number.isFinite(range.max) ? range.max : null
  if (min == null && max == null) return null
  if (min != null && max != null && min > max) return null
  return { min, max }
}

function formatBound(n: number): string {
  return Number.isInteger(n) ? String(n) : String(n).replace('.', ',')
}

/** «90–175 %», «от 90 %» или «до 175 %». Нет диапазона — null. */
export function rangeLabel(range: AllowedRange | null | undefined, unit?: string | null): string | null {
  const bounds = allowedRange(range)
  if (!bounds) return null
  const unitPart = unit ? ` ${unit}` : ''
  if (bounds.min != null && bounds.max != null) return `${formatBound(bounds.min)}–${formatBound(bounds.max)}${unitPart}`
  if (bounds.min != null) return `от ${formatBound(bounds.min)}${unitPart}`
  if (bounds.max == null) return null
  return `до ${formatBound(bounds.max)}${unitPart}`
}
