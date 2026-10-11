// Форма «Создание СТЕ» без React: поля карточки из ответа модуля, подписи под полями, карточка для проверки.
import type { CardIn, CharIn, CharacteristicOut, Issue, Position, Span } from '../../api/v1.ts'
import { rangeLabel, type AllowedRange } from './allowedValues.ts'

/** Поле карточки: значение модуля (initial) и текущее значение человека (value); '' — не заполнено. */
export type FieldState = {
  name: string
  value: string
  initial: string
  unit: string | null
  required: boolean
  options: string[]
  source: Span | null
  status: CharacteristicOut['status']
  normalizedFrom: string | null
  method: CharacteristicOut['method']
  note: string | null
  /** Значение самого поставщика (файл пакета, исправляемая заявка): подпись под полем, иначе null. */
  own: string | null
  /** «Общие сведения» (производитель, модель, страна): своя группа на шаге 4. */
  group: 'general' | null
  /** Единица полным названием по ОКЕИ (формат КТРУ), если модуль её знает. */
  unitName: string | null
  /** То же значение короче в другой единице («1,5 м» при 1500 мм) — для чтения, пока значение модуля не правили. */
  compact: string | null
  /** Допустимый диапазон числа у позиции: подпись «Допустимо: 10–120 мкм» под пустым или исправленным полем. */
  range?: AllowedRange | null
  /** Откуда характеристика: из КТРУ, из шаблона портала (или, вне шаблона, так её пишут на портале), новая. */
  origin: Origin
  /** Свойство из описания вне шаблона категории (`AutofillOut.extra`): свой блок на шаге 4. */
  extra: boolean
  /**
   * Подсказка похожих СТЕ или такой же СТЕ портала (`method: similar`, `twin`): предложение, а не значение.
   * Поле приходит пустым; в карточку значение попадает, только когда поставщик его принял.
   */
  hint: string | null
  /** Значение модели код подтвердил: в цитате и подпись поля, и значение (`CharacteristicOut.checked`). */
  checked?: boolean
  /** Поставщик нажал «Верно» у значения, которое требовало проверки. */
  confirmed?: boolean
  /** Сопоставление значения с КТРУ, полученное от backend. */
  ktruFit?: CharacteristicOut['ktru_fit']
  ktruMatch?: string | null
  kind?: Position['characteristics'][number]['kind']
  /** Поставщик явно ввёл или выбрал значение, даже если оно совпало с исходным. */
  edited?: boolean
}

export type Origin = 'ktru' | 'portal' | 'new'

/** Подпись под полем: error и warn — замечание, source — откуда взято значение. */
export type Helper = { text: string; tone: 'error' | 'warn' | 'source' | 'plain' }

const FIELD_PREFIX = 'characteristics.'
const key = (name: string) => name.trim().toLowerCase()

export function buildFields(chars: CharacteristicOut[], position: Position | null, extra = false): FieldState[] {
  const specs = new Map((position?.characteristics ?? []).map((s) => [key(s.name), s]))
  return chars.map((c) => {
    const spec = specs.get(key(c.name))
    const value = c.value ?? ''
    return {
      name: c.name,
      value,
      initial: value,
      unit: c.unit ?? spec?.unit ?? null,
      required: c.required || Boolean(spec?.required),
      options: c.values?.length ? c.values : (spec?.values ?? []),
      source: c.source,
      status: c.status,
      normalizedFrom: c.normalized_from ?? null,
      method: c.method ?? null,
      note: c.note ?? null,
      own: null,
      group: c.group ?? spec?.group ?? null,
      unitName: c.unit_name ?? null,
      compact: c.compact ?? null,
      range: c.range ?? spec?.range ?? null,
      origin: c.origin ?? 'portal',
      extra,
      hint: null,
      ktruFit: c.ktru_fit ?? null,
      ktruMatch: c.ktru_match ?? null,
      checked: c.checked ?? false,
      kind: spec?.kind,
    }
  })
}

const HINTS = new Set<CharacteristicOut['method']>(['similar', 'twin'])

/** Значение подсказки уходит из поля в предложение: поле пустое, пока поставщик не примет. */
const asHint = (f: FieldState): FieldState => (HINTS.has(f.method) && f.value.trim() ? { ...f, value: '', initial: '', hint: f.value } : f)

/** Поля ответа модуля для мастера: шаблон категории (с характеристиками КТРУ), затем свойства вне шаблона; подсказки — предложениями. */
export function fieldsOf(out: { characteristics: CharacteristicOut[]; extra?: CharacteristicOut[] }, position: Position | null): FieldState[] {
  return [...buildFields(out.characteristics, position), ...buildFields(out.extra ?? [], null, true)].map(asHint)
}

/** Подсказка принята: в поле её значение (кнопкой «Принять» или тем же, введённым руками). */
export const accepted = (f: FieldState) => f.hint != null && f.value.trim() !== '' && f.value === f.hint

/** Непринятые подсказки: поле пустое, предложение есть. */
export const pendingHints = (fields: FieldState[]) => fields.filter((f) => f.hint != null && !f.value.trim())

/** «Принять» и «Принять все»: значение подсказки в пустое поле; без `names` — во все. Своё значение не трогается. */
export function acceptHints(fields: FieldState[], names?: string[]): FieldState[] {
  const only = names ? new Set(names) : null
  return fields.map((f) => (f.hint != null && !f.value.trim() && (!only || only.has(f.name)) ? { ...f, value: f.hint } : f))
}

/** Подпись кнопки: «Принять «750 мм»». */
export const acceptLabel = (f: FieldState) => `Принять «${[f.hint ?? '', f.unit ?? ''].join(' ').trim()}»`

/**
 * Характеристики поставщика поверх ответа модуля: его значение главнее, как в массовой загрузке
 * (`app/batch.py`). Чего модуль не вернул, добавляется полем; проверит его `/check`.
 */
export function withOwn(fields: FieldState[], own: CharIn[], position: Position | null, label: string): FieldState[] {
  const pending = new Map(own.filter((c) => c.value.trim() !== '').map((c) => [key(c.name), c]))
  const mark = (f: FieldState, c: CharIn): FieldState => ({
    ...f,
    value: c.value,
    initial: c.value,
    unit: c.unit ?? f.unit,
    // Название единицы модуля верно только для его единицы: «0,5 см» — не «Миллиметр».
    unitName: c.unit && c.unit !== f.unit ? null : f.unitName,
    compact: null,
    source: null,
    status: 'filled',
    normalizedFrom: null,
    method: null,
    note: null,
    own: label,
    hint: null,
    ktruFit: null,
    ktruMatch: null,
  })
  const out = fields.map((f) => {
    const c = pending.get(key(f.name))
    if (!c) return f
    pending.delete(key(f.name))
    return mark(f, c)
  })
  const extra = [...pending.values()].map((c) => {
    const [f] = buildFields([{ name: c.name, value: c.value, unit: c.unit ?? null, source: null, status: 'filled', required: false, origin: c.origin ?? 'portal' }], position)
    return mark(f, c)
  })
  return [...out, ...extra]
}

/** Человек поменял значение модуля; принятая подсказка — не правка. */
export const changed = (f: FieldState) => (Boolean(f.edited) || f.value !== f.initial) && !accepted(f)

/** Значение модуля в поле, пока его не правили: из текста или принятая подсказка (для короткой записи «= 1,5 м»). */
export const moduleValue = (f: FieldState) => f.value.trim() !== '' && !changed(f)

/** Значение взято из текста поставщика и человек его не менял: поле подсвечивается. */
export const fromText = (f: FieldState) => f.source !== null && !changed(f)

/**
 * Новый ответ модуля (после вопроса) поверх правок человека: изменённое человеком и значения поставщика
 * остаются, у значений поставщика обновляются только перечень и обязательность из новой позиции.
 */
export function mergeFields(prev: FieldState[], next: FieldState[], selectionChanged = false): FieldState[] {
  const before = new Map(prev.map((f) => [key(f.name), f]))
  const merged = next.map((f) => {
    const old = before.get(key(f.name))
    // При другом типе не переносим ответы и подсказки прежней позиции. Число в другой единице
    // тоже нельзя переносить буквально (500 мм не означает 500 м).
    if (selectionChanged) {
      if (!old || (!changed(old) && !old.own) || key(old.unit ?? '') !== key(f.unit ?? '')) return f
      return { ...f, value: old.value, edited: true, source: null, normalizedFrom: null, method: null, note: null, own: old.own, hint: null, ktruFit: null, ktruMatch: null, compact: null }
    }
    if (old?.own) {
      return { ...f, value: old.value, initial: old.initial, unit: old.unit, unitName: old.unit === f.unit ? f.unitName : null, own: old.own, edited: old.edited, source: null, normalizedFrom: null, method: null, note: null, hint: null, ktruFit: null, ktruMatch: null, compact: null }
    }
    // Правка человека и принятая им подсказка остаются: это его решение.
    return old && (changed(old) || accepted(old)) ? { ...f, value: old.value, unit: old.unit, unitName: old.unit === f.unit ? f.unitName : null, edited: true, source: null, normalizedFrom: null, method: null, note: null, hint: null, ktruFit: null, ktruMatch: null, compact: null, ...(accepted(old) ? { hint: old.hint, method: old.method, note: old.note } : {}) } : f
  })
  const names = new Set(next.map((f) => key(f.name)))
  return selectionChanged ? merged : [...merged, ...prev.filter((f) => f.own && !names.has(key(f.name)))]
}

const filled = (f: FieldState) => f.value.trim() !== ''

/** Карточка для /check и заявки: код категории (этап 1) и итоговый код КТРУ (этап 2) — разные поля. */
export function cardFrom(name: string, description: string, fields: FieldState[], categoryCode: string | null, ktruCode: string | null = null): CardIn {
  return {
    name,
    description,
    category_code: categoryCode,
    ktru_code: ktruCode,
    characteristics: fields.filter(filled).map((f) => ({ name: f.name, value: f.value, unit: f.unit, origin: f.origin })),
  }
}

const KTRU_CODE = /^\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{5}(?:\d{3})?$/

/** Коды заявки для показа. Старые заявки без `category_code` хранят код категории в `ktru_code`. */
export function cardCodes(card: { ktru_code?: string | null; category_code?: string | null }): { category: string | null; ktru: string | null } {
  const ktru = card.ktru_code || null
  if (card.category_code) return { category: card.category_code === ktru ? null : card.category_code, ktru }
  if (ktru && !KTRU_CODE.test(ktru)) return { category: ktru, ktru: null }
  return { category: null, ktru }
}

/** Код с названием позиции: «31.01.10.000-00000005 · Стол письменный»; названия нет — один код. */
export function codeWithName(code: string, name: string | null | undefined): string {
  return name?.trim() ? `${code} · ${name.trim()}` : code
}

/**
 * Путь категории в каталоге для подписи: «Товары медицинские / … / Перчатки медицинские». Последний
 * узел, совпадающий с названием категории, не повторяется — название уже рядом с кодом.
 */
export function categoryPath(path: string[] | null | undefined, name?: string | null): string | null {
  const nodes = [...(path ?? [])]
  if (name && nodes.at(-1)?.trim().toLowerCase() === name.trim().toLowerCase()) nodes.pop()
  return nodes.length ? nodes.join(' / ') : null
}

/** Строка кодов для списка заявок: «категория …, КТРУ …». */
export function codesText(card: { ktru_code?: string | null; category_code?: string | null }): string {
  const { category, ktru } = cardCodes(card)
  return [...(category ? [`категория ${category}`] : []), ktru ? `КТРУ ${ktru}` : 'позиция КТРУ не указана'].join(', ')
}

/** Характеристики заявки: изменённое человеком уходит без фрагмента текста, как заполненное им. */
export function acceptedFrom(fields: FieldState[]): CharacteristicOut[] {
  return fields.filter(filled).map((f) => {
    const own = changed(f)
    return {
      name: f.name,
      value: f.value,
      unit: f.unit,
      source: own ? null : f.source,
      normalized_from: own ? null : f.normalizedFrom,
      status: own ? 'filled' : f.status,
      required: f.required,
      method: own ? null : f.method,
      note: own ? null : f.note,
      origin: f.origin,
      ktru_fit: own ? null : f.ktruFit ?? null,
      ktru_match: own ? null : f.ktruMatch ?? null,
      values: f.options,
    }
  })
}

export function issuesByField(issues: Issue[]): { name: Issue[]; fields: Map<string, Issue[]>; other: Issue[] } {
  const out = { name: [] as Issue[], fields: new Map<string, Issue[]>(), other: [] as Issue[] }
  for (const i of issues) {
    if (i.field === 'name') out.name.push(i)
    else if (i.field.startsWith(FIELD_PREFIX)) {
      const name = i.field.slice(FIELD_PREFIX.length)
      out.fields.set(name, [...(out.fields.get(name) ?? []), i])
    } else out.other.push(i)
  }
  return out
}

const hintNote = (f: FieldState) => f.note ?? (f.method === 'twin' ? 'как у такой же СТЕ портала' : 'как у похожих СТЕ портала')

export function fieldHelper(f: FieldState, issue?: Issue, answeredStep?: 2 | 3): Helper {
  // Сведения «вне шаблона категории» у свойства вне шаблона повторяют его подпись: не предупреждение.
  if (issue && !(f.extra && issue.severity === 'info')) {
    // Принятое из подсказки остаётся подписанным и под замечанием: модератор видит, откуда значение.
    const hinted = HINTS.has(f.method) && filled(f) && !changed(f) && !f.own
    const text = hinted ? `${issue.message} Принято из подсказки: ${hintNote(f)}` : issue.message
    return { text, tone: issue.severity === 'block' ? 'error' : 'warn' }
  }
  const range = rangeLabel(f.range, f.unit)
  const withRange = (text: string) => (range ? `${text}. Допустимо: ${range}` : text)
  if (changed(f)) return { text: withRange('Изменено вами'), tone: 'plain' }
  if (f.own) return { text: f.own, tone: 'plain' }
  // Подсказка не из текста: поле пустое, рядом кнопка «Принять» (жёлтое — только из текста).
  if (f.hint != null && !filled(f)) return { text: `Подсказка: ${hintNote(f)}`, tone: 'plain' }
  if (!filled(f)) {
    return f.required
      ? { text: withRange('Обязательная характеристика: в описании не нашли'), tone: 'error' }
      : { text: withRange('В описании не нашли, можно оставить пустым'), tone: 'plain' }
  }
  if (f.method === 'answer') {
    return { text: f.note?.includes('выбранного типа') ? 'Из выбранного вами типа КТРУ' : answeredStep ? `Из вашего ответа на шаге ${answeredStep}` : 'Из вашего ответа на вопрос', tone: 'plain' }
  }
  if (f.confirmed) return { text: 'Подтверждено вами', tone: 'plain' }
  if (f.method === 'llm' && f.checked && f.source) return { text: `Модель нашла в тексте «${f.source.text}»: подпись и значение совпали`, tone: 'source' }
  if (f.method === 'llm') return { text: f.source ? `Модель по вашему тексту «${f.source.text}»: проверьте` : 'Модель: проверьте значение по вашему тексту', tone: 'warn' }
  if (f.normalizedFrom) return { text: `Из «${f.normalizedFrom}» в описании, приведено к значению справочника`, tone: 'source' }
  // В тексте значения нет: поставщик принял подсказку такой же или похожих СТЕ той же категории.
  if (HINTS.has(f.method)) return { text: `Принято из подсказки: ${hintNote(f)}. Проверьте`, tone: 'plain' }
  if (f.extra) {
    const kind = f.origin === 'new' ? 'Новая характеристика' : 'Так пишут на портале'
    return { text: f.source ? `${kind}. Из описания: «${f.source.text}»` : kind, tone: 'source' }
  }
  if (f.source) return { text: `Из описания: «${f.source.text}»`, tone: 'source' }
  return { text: '', tone: 'plain' }
}

/** Сколько всего мешает отправке: блокирующие замечания и пустые обязательные поля, о которых модуль не сказал. */
export function blocking(issues: Issue[], fields: FieldState[]): number {
  const blocked = issues.filter((i) => i.severity === 'block')
  const covered = new Set(blocked.map((i) => i.field))
  const empty = fields.filter((f) => f.required && !filled(f) && !covered.has(FIELD_PREFIX + f.name))
  return blocked.length + empty.length
}

// Те же невидимые символы и пробелы, что убирает бэкенд (app/textclean.py): иначе фрагменты в ответе
// модуля не совпадут с текстом на экране. Коды символов числами: невидимый символ в исходнике не прочитать.
const chars = (...ranges: [number, number][]) => ranges.map(([a, b]) => `${String.fromCharCode(a)}-${String.fromCharCode(b)}`).join('')
const INVISIBLE = new RegExp(
  `[${chars([0xad, 0xad], [0x180e, 0x180e], [0x200b, 0x200f], [0x202a, 0x202e], [0x2060, 0x2064], [0xfeff, 0xfeff])}]`,
  'g',
)
const SPACES = new RegExp(
  `[${chars([0xa0, 0xa0], [0x1680, 0x1680], [0x2000, 0x200a], [0x202f, 0x202f], [0x205f, 0x205f], [0x3000, 0x3000])}]`,
  'g',
)

/** Вставленный текст: первая непустая строка — наименование, остальное — описание. */
export function splitPasted(text: string): { name: string; description: string } {
  const lines = text.replace(INVISIBLE, '').replace(SPACES, ' ').split(/\r?\n/)
  const first = lines.findIndex((l) => l.trim() !== '')
  if (first < 0) return { name: '', description: '' }
  return {
    name: lines[first].trim(),
    description: lines
      .slice(first + 1)
      .join('\n')
      .trim(),
  }
}

export function joinPasted(name: string, description: string): string {
  return description.trim() ? `${name}\n${description}` : name
}

/**
 * Характеристики заявки для модератора: ровно то, что в карточке (это и проверял модуль); фрагмент
 * текста берётся из принятых модулем, только если имя и значение совпали.
 */
export function submissionFields(card: CardIn, accepted: CharacteristicOut[]): FieldState[] {
  const chars = (card.characteristics ?? []).map(
    (c): CharacteristicOut =>
      accepted.find((a) => a.name === c.name && a.value === c.value) ?? {
        name: c.name,
        value: c.value,
        unit: c.unit ?? null,
        source: null,
        status: 'filled',
        required: false,
        origin: c.origin ?? 'portal',
      },
  )
  // Новое свойство поставщика модератор видит с пометкой «новая»; портальное вне шаблона — по замечанию модуля.
  return buildFields(chars, null).map((f) => ({ ...f, extra: f.origin === 'new' }))
}
