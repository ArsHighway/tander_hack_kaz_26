import type { AuditRule, PortalReport } from '../../api/v1.ts'
import { NBSP, decimal, plural } from '../../lib/format.ts'

/** Значение метрики в её единице: доли — проценты, задержки — миллисекунды, остальное — число. */
export function formatMetric(key: string, value: number): string {
  if (key.startsWith('latency_')) return `${decimal(value, 1)}${NBSP}мс`
  if (key.startsWith('questions') || key === 'min_score') return decimal(value, 2)
  if (key === 'manual_required' || key === 'manual_fields') return decimal(value, 1)
  return `${decimal(value * 100, 1)}${NBSP}%`
}

/** Правило «ошибки ввода»: число в характеристике против числа в наименовании (app/portal/audit.py). */
export const CONTRADICTION_RULE = 'значение не противоречит наименованию товара'

const count = new Intl.NumberFormat('ru-RU')

/** «5 108 из 8 520 карточек нарушают хотя бы одно из правил модерации, которые проверяет модуль». */
export function portalHeadline(r: PortalReport): string {
  const a = r.audit
  return `${count.format(a.with_issues)} из ${count.format(a.cards)} ${plural(a.cards, 'карточки', 'карточек', 'карточек')} нарушают хотя бы одно из правил модерации, которые проверяет модуль`
}

/** Ошибки ввода показываются отдельно: это находки, которые человек проверит по ссылке. */
export function splitRules(r: PortalReport): { slips: AuditRule | null; other: AuditRule[] } {
  return {
    slips: r.audit.rules.find((x) => x.rule === CONTRADICTION_RULE) ?? null,
    other: r.audit.rules.filter((x) => x.rule !== CONTRADICTION_RULE),
  }
}
