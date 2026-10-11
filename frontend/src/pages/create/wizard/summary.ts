import type { AutofillOut, Issue } from '../../../api/v1.ts'
import { ktruText, type Chain } from '../chainView.ts'
import { changed, type FieldState } from '../formModel.ts'
import { readiness } from './groups.ts'

/** У первого этапа код может быть КПГЗ или служебным кодом типа: КТРУ берём из второго. */
export function finalKtru(out: AutofillOut): string | null {
  return out.chain?.ktru?.final_code ?? (!out.chain && /^\d{2}\.\d{2}\.\d{2}\.\d{3}-\d+$/.test(out.ktru.final_code ?? '') ? out.ktru.final_code : null)
}

export function portalLink(chain: Chain | null | undefined): string {
  if (!chain?.kpgz || chain.category_via === 'none') return 'связи нет'
  if (chain.category_via === 'ktru_link') return `${chain.kpgz} (по связи с КТРУ)`
  if (chain.category_via === 'search') return `${chain.kpgz} (найдена поиском, связь с КТРУ не подтверждена)`
  return `${chain.kpgz} (источник связи не указан)`
}

export function ktruSummary(out: AutofillOut, fields: FieldState[], checked: Issue[] | null | 'failed') {
  const code = finalKtru(out)
  const ready = readiness(fields)
  const added = fields.filter((f) => f.origin === 'new' && f.value.trim()).length
  const outside = fields.filter((f) => {
    if (f.origin !== 'ktru' || !f.value.trim()) return false
    if (!changed(f) && (f.ktruFit === 'not_in_list' || f.ktruFit === 'out_of_range')) return true
    return Array.isArray(checked) && checked.some((i) => i.field === `characteristics.${f.name}` && i.severity !== 'info' &&
      i.rule === 'значение соответствует выбранной позиции КТРУ' && (i.kind === 'not_in_list' || i.kind === 'mismatch'))
  }).map((f) => f.name)
  const title = code ? `Соответствие КТРУ ${code}` : `Код КТРУ не определён${out.chain ? `: ${ktruText(out.chain).value}` : ''}`
  const verified = Boolean(code) && Array.isArray(checked)
  return {
    title,
    text: `Обязательные: заполнено ${ready.done} из ${ready.total}, вне КТРУ ${verified ? outside.length : 'не проверено'}, новых ${added}`,
    outside,
    missing: ready.left,
    added,
    verified,
  }
}
