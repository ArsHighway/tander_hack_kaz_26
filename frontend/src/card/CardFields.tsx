import { Autocomplete, Box, Button, ClickAwayListener, IconButton, MenuItem, TextField, Tooltip, Typography } from '@mui/material'
import { useState, type ReactNode } from 'react'
import type { Issue } from '../api/v1.ts'
import { acceptLabel, fieldHelper, moduleValue, type FieldState, type Helper } from '../pages/create/formModel.ts'
import { fieldStatus, ktruRelation, numericField, valueSource, type FieldStatus } from '../pages/create/ktruFieldModel.ts'
import { TOKENS } from '../theme'
import { fieldId } from './fieldId'
import { OriginMark } from './OriginMark'
import { SourceIcon } from './SourceIcon'

const HELPER_COLOR: Record<Helper['tone'], string> = {
  error: 'error.main',
  warn: 'warning.dark',
  source: 'text.secondary',
  plain: 'text.secondary',
}

function HelperText({ helper, fix, children }: { helper: Helper; fix?: ReactNode; children?: ReactNode }) {
  if (!helper.text && !fix && !children) return null
  return (
    <Box component="span" className="flex flex-col gap-1">
      {children}
      <Box component="span" className="flex flex-wrap items-center gap-x-2" sx={{ color: HELPER_COLOR[helper.tone] }}>
        {helper.text}
        {fix}
      </Box>
    </Box>
  )
}

const fixLabel = (value: string) => `Исправить на «${value}»`

/** Высота поля кита — 44 px при корне 16 px; отступы считаются от шрифта поля, поэтому высота одна при любом шрифте. */
const FIELD_PAD = 'calc((2.75rem - 1.4375em) / 2)'
/** Поле таблицы характеристик: текст того же размера, что имя характеристики слева, а не крупнее его. */
const CELL_FIELD = {
  '& .MuiInputBase-root': { fontSize: '0.9375rem' },
  '& .MuiInputBase-input': { py: FIELD_PAD },
}

type NameProps = { value: string; issue?: Issue; readOnly?: boolean; onChange?: (value: string) => void }

export function NameField({ value, issue, readOnly, onChange }: NameProps) {
  const fix = issue?.fix?.value
  const helper: Helper = issue
    ? { text: issue.message, tone: issue.severity === 'block' ? 'error' : 'warn' }
    : { text: readOnly ? '' : 'Только название товара: размеры и другие значения указываются в характеристиках', tone: 'plain' }
  return (
    <TextField
      label="Наименование"
      fullWidth
      size="small"
      sx={{ '& .MuiInputBase-multiline': { py: FIELD_PAD } }}
      // Длинное наименование переносится и видно целиком; перевода строки в наименовании нет — Enter его не ставит.
      multiline
      minRows={1}
      maxRows={4}
      value={value}
      error={helper.tone === 'error'}
      onChange={onChange ? (e) => onChange(e.target.value.replace(/\s*\n\s*/g, ' ')) : undefined}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.preventDefault()
      }}
      helperText={
        <HelperText
          helper={helper}
          fix={
            fix && !readOnly && onChange ? (
              <Button size="small" onClick={() => onChange(fix)}>
                {fixLabel(fix)}
              </Button>
            ) : undefined
          }
        />
      }
      slotProps={{ input: { readOnly } }}
    />
  )
}

export function KtruField({ value, helper, label = 'Позиция КТРУ' }: { value: string; helper?: ReactNode; label?: string }) {
  return (
    <TextField
      label={label}
      fullWidth
      size="small"
      sx={{ '& .MuiInputBase-input': { py: FIELD_PAD } }}
      value={value}
      helperText={helper}
      slotProps={{ input: { readOnly: true } }}
    />
  )
}

type FieldsProps = {
  fields: FieldState[]
  issues: Map<string, Issue[]>
  readOnly?: boolean
  onChange?: (name: string, value: string, unit?: string | null) => void
  /** «Верно» у значения, которое требует проверки: поставщик подтверждает его сам. */
  onConfirm?: (name: string) => void
  onFocusField?: (name: string | null) => void
  /** На каком шаге мастера человек ответил о характеристике: подпись «Ваш ответ на шаге 3». */
  answeredAt?: Record<string, 2 | 3>
  /** До первой проверки пустое обязательное поле не красное: звёздочка у имени и подсказка, что указать. */
  quietRequired?: boolean
}

// Статус поля — кружок цветом статуса портала и слово; подпись — тёмный оттенок (мелкий текст 4,5:1).
const STATUS_DOT: Record<FieldStatus, { dot: string; text: string }> = {
  подтверждено: { dot: '#0d9b68', text: '#0b7f55' },
  предложено: { dot: TOKENS.acc, text: TOKENS.acc },
  'не заполнено': { dot: TOKENS.field, text: TOKENS.mute },
  'требует проверки': { dot: '#f67319', text: '#7a3b00' },
}

// Ячейка значения с замечанием подсвечена, как расхождение в таблице сравнения UI-кита.
const CELL_TINT = { error: 'rgba(219, 43, 33, 0.06)', warn: 'rgba(246, 115, 25, 0.08)' } as const

/**
 * Колонки: характеристика, значение, единица, статус. Имя и значение почти поровну: длинное имя («Количество
 * выдвижных ящиков») встаёт в одну строку, а поле не тянется на полэкрана; статус и единица — постоянной ширины.
 * Ширину меряет контейнер (`@container`): от 640 px — четыре колонки, уже — строка в столбик.
 */
const GRID =
  'grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 @min-[640px]:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)_3.5rem_11rem] @min-[640px]:gap-x-4'

/**
 * Откуда значение и почему — за значком «документ с i» из UI-кита: подсказка при наведении, фокусе и нажатии (на
 * телефоне). На экране остаётся статус, а не абзац источников; читалка экрана получает их в названии кнопки.
 */
function WhyButton({ lines }: { lines: string[] }) {
  const [open, setOpen] = useState(false)
  if (!lines.length) return null
  return (
    <ClickAwayListener onClickAway={() => setOpen(false)}>
      <span>
        <Tooltip
          open={open}
          onOpen={() => setOpen(true)}
          onClose={() => setOpen(false)}
          disableTouchListener
          arrow
          placement="top"
          title={
            <Box component="span" className="flex flex-col gap-0.5">
              {lines.map((l) => (
                <span key={l}>{l}</span>
              ))}
            </Box>
          }
        >
          <IconButton aria-label={`Откуда значение: ${lines.join('. ')}`} onClick={() => setOpen(true)} sx={{ p: 0.5, color: TOKENS.acc }}>
            <SourceIcon size={22} />
          </IconButton>
        </Tooltip>
      </span>
    </ClickAwayListener>
  )
}

function StatusCell({ status, why }: { status: FieldStatus; why: string[] }) {
  const c = STATUS_DOT[status]
  return (
    <Box component="span" className="flex min-w-0 items-center gap-1.5" sx={{ fontSize: '0.8125rem', lineHeight: '1rem' }}>
      <Box component="span" aria-hidden className="inline-flex items-center gap-1.5" sx={{ color: c.text, fontWeight: 600 }}>
        <Box component="span" sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: c.dot, flex: 'none' }} />
        {status}
      </Box>
      <WhyButton lines={why} />
    </Box>
  )
}

/** Что писать под полем: только то, что требует действия; откуда значение — в колонке справа. */
function noteOf(f: FieldState, issue: Issue | undefined, relation: Helper | null, helper: Helper, hint: string | null): Helper {
  const empty = !f.value.trim()
  if (issue) return helper
  if (relation) return empty || relation.tone === 'error' ? relation : { text: '', tone: 'plain' }
  if (helper.tone === 'error' || helper.tone === 'warn' || hint !== null) return helper
  return { text: '', tone: 'plain' }
}

/**
 * Характеристики карточки таблицей, как «Сравнение характеристик» в UI-ките портала: имя, поле значения, единица и
 * откуда значение. Под полем — только замечание, исправление, подсказка или перечень КТРУ. У модератора вместо
 * полей — значения текстом.
 */
export function CharacteristicFields({ fields, issues, readOnly, onChange, onConfirm, onFocusField, answeredAt, quietRequired }: FieldsProps) {
  return (
    <Box className="@container">
      <Box aria-hidden className={`${GRID} hidden px-3 py-2 @min-[640px]:grid`} sx={{ bgcolor: TOKENS.accSoft, fontSize: '0.875rem', color: TOKENS.ink2 }}>
        <span>Характеристика</span>
        <span>Значение</span>
        <span>Ед. изм.</span>
        <span>Статус</span>
      </Box>
      {fields.map((f) => {
        // Пустое обязательное до первой проверки: замечание «не заполнена» не показываем красным, звёздочки хватает.
        const quiet = Boolean(quietRequired) && f.required && !f.value.trim() && f.hint == null
        const issue = quiet ? issues.get(f.name)?.find((i) => i.severity !== 'block') : issues.get(f.name)?.[0]
        const relation = ktruRelation(f)
        const base = issue ? fieldHelper(f, issue, answeredAt?.[f.name]) : relation ?? fieldHelper(f, undefined, answeredAt?.[f.name])
        const helper: Helper = quiet && base.tone === 'error' ? { text: 'Обязательная характеристика: укажите значение', tone: 'plain' } : base
        const status = fieldStatus(f, issues.get(f.name))
        const numeric = numericField(f)
        const select = f.options.length > 0 && !numeric
        const invalid = f.origin === 'ktru' && (issue?.kind === 'not_in_list' || (!f.edited && f.ktruFit === 'not_in_list'))
        const fix = issue?.fix?.value
        // Подсказка похожих СТЕ или двойника: поле пустое, значение — только по «Принять».
        const hint = f.hint != null && !f.value.trim() ? f.hint : null
        const empty = !f.value.trim()
        const note = noteOf(f, issue, relation, helper, hint)
        const tone = note.tone === 'error' ? 'error' : note.tone === 'warn' || status === 'требует проверки' ? 'warn' : null
        const where = valueSource(f, answeredAt?.[f.name]) || (empty ? 'В описании не нашли' : '')
        // За значком: откуда значение и почему оно такое (соответствие КТРУ, приведение к справочнику).
        const why = [where]
        if (relation && relation.text !== note.text && relation.text !== issue?.message) why.push(relation.text)
        else if (!relation && f.normalizedFrom) why.push('Приведено к значению справочника')
        const confirmable = !readOnly && onConfirm && !empty && status === 'требует проверки' && !invalid && issue?.severity !== 'block'
        const actions =
          !readOnly && onChange && (fix || hint !== null || (invalid && !numeric) || confirmable) ? (
            <>
              {confirmable && (
                <Button size="small" variant="outlined" onClick={() => onConfirm?.(f.name)}>
                  Верно
                </Button>
              )}
              {fix && (
                <Button size="small" onClick={() => onChange(f.name, fix, issue?.fix?.unit ?? undefined)}>
                  {fixLabel(fix)}
                </Button>
              )}
              {hint !== null && (
                // Длинное значение переносится по левому краю, как подпись над кнопкой.
                <Button size="small" onClick={() => onChange(f.name, hint)} sx={{ textAlign: 'left', justifyContent: 'flex-start' }}>
                  {acceptLabel(f)}
                </Button>
              )}
              {invalid && !numeric && f.options.filter((o) => o !== f.value).map((o) => (
                <Button key={o} size="small" variant="outlined" onClick={() => onChange(f.name, o)} sx={{ textAlign: 'left' }}>
                  Выбрать «{o}»
                </Button>
              ))}
            </>
          ) : undefined
        // Статус и источник видны в колонке справа; читалке экрана — здесь, вместе с замечанием.
        const spoken = <span className="sr-only">{`${status}. ${where}`}</span>
        const helperText = (
          <Box component="span" className="flex flex-col gap-1">
            {spoken}
            <HelperText helper={{ text: note.text, tone: note.tone === 'source' ? 'plain' : note.tone }} fix={actions} />
            {issue && relation && relation.text !== issue.message && (
              <Box component="span" sx={{ color: HELPER_COLOR[relation.tone] }}>
                {relation.text}
              </Box>
            )}
            {f.origin === 'new' && <Box component="span" sx={{ color: 'text.secondary' }}>Новая характеристика: её проверит модератор</Box>}
          </Box>
        )
        const common = {
          label: f.name,
          required: f.required && !readOnly,
          error: note.tone === 'error',
          helperText,
          onFocus: onFocusField ? () => onFocusField(f.name) : undefined,
          onBlur: onFocusField ? () => onFocusField(null) : undefined,
        }
        let control: ReactNode
        if (readOnly) {
          control = (
            <Box className="flex flex-col gap-1 @min-[640px]:pt-3">
              <Typography sx={{ fontSize: '0.9375rem', lineHeight: '1.25rem', overflowWrap: 'anywhere', color: f.value ? TOKENS.ink : TOKENS.mute }}>
                {f.value || 'не указано'}
              </Typography>
              {issue && <Box component="span" sx={{ fontSize: '0.8125rem', color: HELPER_COLOR[helper.tone] }}>{helper.text}</Box>}
            </Box>
          )
        } else if (f.group === 'general' && f.options.length > 0) {
          // «Общие сведения»: перечень — подсказки из СТЕ категории, а не ограничение (новый производитель — не ошибка).
          control = (
            <Autocomplete
              id={fieldId(f.name)}
              freeSolo
              size="small"
              // Отступы поля с подсказками — у самого Autocomplete: его правило для маленького поля сильнее sx поля.
              sx={{ '& .MuiOutlinedInput-root.MuiInputBase-sizeSmall': { fontSize: '0.9375rem', py: `calc(${FIELD_PAD} - 2.5px)` } }}
              options={f.options}
              inputValue={f.value}
              onInputChange={(_, value) => value !== f.value && onChange?.(f.name, value)}
              renderInput={(params) => (
                <TextField
                  {...params}
                  {...common}
                  placeholder="Не указано"
                  slotProps={{ ...params.slotProps, inputLabel: { ...params.slotProps.inputLabel, className: 'sr-only' } }}
                />
              )}
            />
          )
        } else {
          const options = select && f.value && !f.options.includes(f.value) ? [...f.options, f.value] : f.options
          control = (
            <TextField
              {...common}
              id={fieldId(f.name)}
              select={select}
              autoComplete="off"
              fullWidth
              size="small"
              sx={CELL_FIELD}
              value={f.value}
              placeholder={empty ? 'Не указано' : undefined}
              onChange={onChange ? (e) => onChange(f.name, e.target.value) : undefined}
              slotProps={{
                // Имя поля видно в колонке слева: подпись самого поля остаётся для читалки экрана.
                inputLabel: { className: 'sr-only' },
                htmlInput: numeric && !select ? { inputMode: 'decimal' } : undefined,
                select: select
                  ? { displayEmpty: true, renderValue: (v: unknown) => (v ? String(v) : <Box component="span" sx={{ color: TOKENS.mute }}>Не указано</Box>) }
                  : undefined,
              }}
            >
              {select && [
                <MenuItem key="" value="" sx={{ color: TOKENS.mute }}>
                  Не указано
                </MenuItem>,
                ...options.map((o) => (
                  <MenuItem key={o} value={o} disabled={!f.options.includes(o)}>
                    {f.options.includes(o) ? o : `${o} (нет в справочнике)`}
                  </MenuItem>
                )),
              ]}
            </TextField>
          )
        }
        return (
          <Box key={f.name} role="group" aria-label={f.name} className={`${GRID} items-start px-3 py-3`} sx={{ borderBottom: `1px solid ${TOKENS.line}` }}>
            <Box
              aria-hidden
              className="col-span-2 pb-1 @min-[640px]:col-span-1 @min-[640px]:pt-3 @min-[640px]:pb-0"
              sx={{ fontSize: '0.9375rem', fontWeight: 600, lineHeight: '1.25rem', color: tone === 'error' ? TOKENS.err : TOKENS.ink, overflowWrap: 'anywhere' }}
            >
              {f.name}
              {(f.origin === 'ktru' || f.extra) && <OriginMark origin={f.origin} />}
              {f.required && !readOnly && (
                <Box component="span" sx={{ color: TOKENS.err, ml: 0.25 }}>
                  *
                </Box>
              )}
            </Box>
            <Box className="min-w-0" sx={{ bgcolor: tone ? CELL_TINT[tone] : undefined, m: tone ? -1 : 0, p: tone ? 1 : 0 }}>
              {control}
            </Box>
            <Box aria-hidden className="pt-3" sx={{ fontSize: '0.9375rem', lineHeight: '1.25rem', color: TOKENS.ink2, whiteSpace: 'nowrap', minWidth: 0 }}>
              {f.unit && !select ? f.unit : ''}
              {/* «= 1,5 м» при 1500 мм — пока значение модуля не правили, пересчёт верен. */}
              {f.unit && f.compact && moduleValue(f) && (
                <Box component="span" sx={{ display: 'block', fontSize: '0.8125rem', color: TOKENS.mute }}>
                  = {f.compact}
                </Box>
              )}
            </Box>
            {/* Статус по центру поля: ячейка высотой с поле (44 px), а не отступ сверху — значок выше строки текста. */}
            <Box className="col-span-2 pt-1 @min-[640px]:col-span-1 @min-[640px]:flex @min-[640px]:min-h-11 @min-[640px]:items-center @min-[640px]:pt-0">
              <StatusCell status={status} why={why.filter(Boolean)} />
            </Box>
          </Box>
        )
      })}
    </Box>
  )
}
