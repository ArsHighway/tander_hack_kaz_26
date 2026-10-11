// Предпросмотр «как на портале»: что увидят заказчики на странице СТЕ после модерации.
import { categoryImageUrl } from '../../../api/v1.ts'
import type { Chain } from '../chainView.ts'
import type { FieldState } from '../formModel.ts'
import { ktruValue } from './ktruFormat.ts'
import { portalLink } from './summary.ts'

/** `fallback` — картинка категории, если своё фото не загрузится (его могла убрать очистка). */
export type PreviewPhoto = { src: string | null; kind: 'own' | 'category' | 'none'; caption: string | null; fallback: string | null }

export type PreviewView = {
  crumbs: string[]
  title: string
  photo: PreviewPhoto
  facts: { label: string; value: string; missing: boolean }[]
  rows: { name: string; value: string; unit: string | null }[]
  extra: { label: string; value: string }[]
}

export type PreviewInput = {
  name: string
  fields: FieldState[]
  chain: Chain | null
  /** Своё фото поставщика (сохранено на сервере), иначе null. */
  photoUrl: string | null
  ktruCode: string | null
}

/** Сведения под заголовком, как на странице СТЕ: модель, производитель, страна (род — для «не указан/а»). */
const FACTS: { label: string; feminine: boolean }[] = [
  { label: 'Модель', feminine: true },
  { label: 'Производитель', feminine: false },
  { label: 'Страна происхождения', feminine: true },
]

function photo(input: PreviewInput): PreviewPhoto {
  // Так делает сам портал: у СТЕ без фото — картинка категории.
  const portalId = input.chain?.portal_id
  const category = portalId != null ? categoryImageUrl(portalId) : null
  if (input.photoUrl) return { src: input.photoUrl, kind: 'own', caption: null, fallback: category }
  if (category) return { src: category, kind: 'category', caption: 'картинка категории, пока нет вашего фото', fallback: null }
  return { src: null, kind: 'none', caption: null, fallback: null }
}

function ktru(input: PreviewInput): string {
  if (input.ktruCode) return input.ktruCode
  // Категория ещё не выбрана или позицию КТРУ ещё уточняют — кода пока нет, но это не «нет в КТРУ».
  if (!input.chain || input.chain.ktru?.question) return 'уточняется на шаге 3'
  // Позиции КТРУ у категории есть, но в справочник модуля не загружены: «нет в КТРУ» было бы неправдой.
  const missing = input.chain.ktru_missing ?? (input.chain.ktru_linked ? 'not_loaded' : 'no_links')
  // Как в живой карточке: позиции КТРУ у категории есть, но в справочник модуля не загружены.
  if (!input.chain.ktru && missing === 'not_loaded') return 'нет в справочнике'
  return 'нет в КТРУ'
}

export function portalPreview(input: PreviewInput): PreviewView {
  // Сведение — поле «Общих сведений», а у категорий, где стандарт сам задаёт «Страну происхождения»,
  // — эта характеристика: иначе превью писало бы «не указана» рядом со строкой «Россия».
  const byName = (label: string) =>
    input.fields.find((f) => f.group === 'general' && f.name === label) ?? input.fields.find((f) => f.name === label)
  const shown = new Set<FieldState>()
  const facts = FACTS.map(({ label, feminine }) => {
    const f = byName(label)
    if (f) shown.add(f)
    const value = f?.value.trim()
    if (value) return { label, value, missing: false }
    const none = feminine ? 'не указана' : 'не указан'
    // Поле есть на шаге 4 — подсказываем, где заполнить; у позиции его нет — просто «не указан».
    return { label, value: f ? `${none}, заполните на шаге 4` : none, missing: true }
  })
  const chain = input.chain
  return {
    crumbs: chain?.path ?? [],
    title: input.name,
    photo: photo(input),
    facts: [{ label: 'ID СТЕ', value: 'после модерации', missing: false }, ...facts],
    rows: input.fields
      // поле, показанное сведением, строкой таблицы не повторяется
      .filter((f) => f.group !== 'general' && !shown.has(f) && f.value.trim())
      .map((f) => ({ name: f.name, value: ktruValue(f.value.trim()), unit: f.unitName ?? f.unit })),
    extra: [
      { label: 'КТРУ', value: ktru(input) },
      { label: 'КПГЗ', value: portalLink(chain) },
      { label: 'ОКПД2', value: chain?.okpd2 ?? 'не определён' },
      { label: 'СПГЗ', value: chain?.spgz.length ? chain.spgz.join(', ') : 'нет' },
    ],
  }
}
