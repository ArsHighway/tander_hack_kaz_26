import { Box, Button, Typography } from '@mui/material'
import { IconHelpCircle } from '@tabler/icons-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, type KtruState, type Position } from '../../../api/v1.ts'
import { MONO, TOKENS } from '../../../theme'
import { CONFIRM, NOTHING_FITS } from '../ktruView.ts'
import { Kbd } from './Kbd'
import { KtruTrail } from './KtruTrail'
import { optionIndex, shouldHandleKey } from './keys.ts'
import { likelyOption, optionsOf, questionText, type Variant } from './options.ts'
import { PositionsPreview } from './PositionsPreview'

type Props = {
  state: KtruState
  stage: 'category' | 'ktru'
  busy: boolean
  /** Вариант, выбранный в прошлый раз (возврат на пройденный шаг). */
  chosen?: string | null
  onAnswer: (characteristic: string, value: string) => void
  /** Шаг 3: вернуться к шагу категории. */
  onBack?: () => void
}

/** Больше кандидатов таблицей не показать: человек их не разберёт, хватит числа «останется N». */
const PREVIEW_MAX = 12

function codesLine(v: Variant): string | null {
  if (!v.codes.length) return null
  return v.codes.length === 1 ? v.codes[0] : `${v.codes[0]} и ещё ${v.codes.length - 1}`
}

/** Шаги 2 и 3: вопрос модуля, варианты с кодом и тем, что останется; клавиши 1…6 и Enter. */
export function StepQuestion({ state, stage, busy, chosen = null, onAnswer, onBack }: Props) {
  const question = state.question
  const [loaded, setLoaded] = useState<{ state: KtruState; positions: Record<string, Position> } | null>(null)
  const positions = loaded?.state === state ? loaded.positions : {}
  const variants = optionsOf(state, stage, positions)
  useEffect(() => {
    if (stage !== 'category' || state.question?.characteristic !== CONFIRM) return
    let active = true
    const codes = state.question.options.filter((o) => o.value !== NOTHING_FITS).map((o) => o.value)
    void Promise.all(codes.map((code) => api.ktru(code).catch(() => null))).then((items) => {
      if (active) setLoaded({ state, positions: Object.fromEntries(items.filter((p): p is Position => p !== null).map((p) => [p.code, p])) })
    })
    return () => { active = false }
  }, [state, stage])
  // Вопрос о характеристике: кандидаты целиком (≤ 12), чтобы показать, какие останутся при каждом ответе.
  const [candidates, setCandidates] = useState<{ state: KtruState; list: Position[] } | null>(null)
  useEffect(() => {
    const q = state.question
    if (!q || q.characteristic === CONFIRM || state.candidates.length < 2 || state.candidates.length > PREVIEW_MAX) return
    let active = true
    void Promise.all(state.candidates.map((c) => api.ktru(c.code).catch(() => null))).then((items) => {
      if (active) setCandidates({ state, list: items.filter((p): p is Position => p !== null) })
    })
    return () => { active = false }
  }, [state])
  // Самый вероятный тип по модели — первый и уже отмечен: Enter соглашается, выбрать другой — клик.
  const [picked0, setSelected] = useState<string | null>(chosen)
  const selected = picked0 ?? likelyOption(state)
  const formRef = useRef<HTMLFormElement>(null)
  const text = questionText(state, stage)

  const confirm = (value: string | null) => {
    if (question && value !== null && !busy) onAnswer(question.characteristic, value)
  }

  const latest = useRef({ variants, selected, confirm })
  useEffect(() => {
    latest.current = { variants, selected, confirm }
  })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || !shouldHandleKey(e.target, formRef.current)) return
      const { variants: list, selected: current, confirm: send } = latest.current
      const index = optionIndex(e.key, list.length)
      if (index !== null) {
        e.preventDefault()
        setSelected(list[index].value)
        formRef.current?.querySelectorAll<HTMLInputElement>('input[type=radio]')[index]?.focus()
      } else if (e.key === 'Enter' && current !== null && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault()
        send(current)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (!question) return null
  const submit = (e: FormEvent) => {
    e.preventDefault()
    confirm(selected)
  }
  const primary = stage === 'category' ? 'Выбрать тип товара' : 'Выбрать и перейти к характеристикам'
  const choosing = question.characteristic === CONFIRM
  // Короткие ответы («Да», «Нет», «Белая») — плитками, до шести; переносятся в следующий ряд. Длиннее — строками.
  const tiles = !choosing && variants.length <= 6 && variants.every((v) => v.title.length <= 28 && !v.reasons.length)
  const picked = variants.find((v) => v.value === selected) ?? null
  const preview = candidates?.state === state ? candidates.list : []

  return (
    <Box component="form" ref={formRef} onSubmit={submit} className="flex flex-col gap-4">
      {/* Вопрос выделен голубым блоком со значком: сразу видно, что от человека ждут ответа. */}
      <Box className="flex items-start gap-3 p-4 md:p-5" sx={{ bgcolor: TOKENS.accSoft }}>
        <Box component="span" aria-hidden sx={{ color: TOKENS.acc, flex: 'none', mt: '2px' }}>
          <IconHelpCircle size={30} stroke={1.5} />
        </Box>
        <Box className="min-w-0">
          <Typography component="h2" sx={{ fontSize: { xs: '1.25rem', md: '1.5rem' }, fontWeight: 700, lineHeight: 1.3, overflowWrap: 'anywhere' }}>
            {text.title}
          </Typography>
          <Typography sx={{ mt: 0.5, color: TOKENS.ink2, maxWidth: '68ch' }}>{text.why}</Typography>
        </Box>
      </Box>
      <KtruTrail trail={state.trail ?? []} />
      <Box
        component="fieldset"
        disabled={busy}
        sx={
          tiles
            ? { m: 0, p: 0, border: 'none', minWidth: 0, display: 'grid', gap: 1.5, gridTemplateColumns: { xs: '1fr', sm: `repeat(${variants.length <= 3 ? variants.length : 3}, minmax(0, 1fr))` } }
            : { m: 0, p: 0, border: `1px solid ${TOKENS.line}`, borderRadius: 0, overflow: 'hidden', minWidth: 0 }
        }
      >
        <Box component="legend" className="sr-only">
          {text.title}
        </Box>
        {variants.map((v, i) => {
          const on = selected === v.value
          const code = codesLine(v)
          return (
            <Box
              component="label"
              key={v.value}
              sx={{
                display: 'grid',
                gridTemplateColumns: tiles ? '20px minmax(0,1fr)' : { xs: '20px minmax(0,1fr)', sm: '20px minmax(0,1fr) auto' },
                columnGap: 1.5,
                rowGap: 0.5,
                alignItems: 'center',
                px: tiles ? 2.5 : 1.75,
                py: tiles ? 2.25 : 1.5,
                ...(tiles
                  ? { border: `${on ? 2 : 1}px solid ${on ? TOKENS.acc : TOKENS.line}`, m: on ? 0 : '1px' }
                  : { borderTop: i ? `1px solid ${TOKENS.line}` : 'none' }),
                bgcolor: on ? TOKENS.accSoft : '#ffffff',
                cursor: busy ? 'default' : 'pointer',
                '&:hover': busy || on ? undefined : { bgcolor: '#f6f7f9' },
                '&:has(input:focus-visible)': { outline: `2px solid ${TOKENS.acc}`, outlineOffset: '-2px' },
              }}
            >
              <input
                type="radio"
                name={`answer-${stage}`}
                className="sr-only"
                value={v.value}
                checked={on}
                onChange={() => setSelected(v.value)}
              />
              <Box
                aria-hidden
                sx={{
                  width: 16,
                  height: 16,
                  borderRadius: '50%',
                  // Отмеченный вариант портала — красный (UI-кит, 03_Controls).
                  border: on ? `5px solid ${TOKENS.cta}` : `1.5px solid ${TOKENS.field}`,
                  bgcolor: '#ffffff',
                  alignSelf: 'start',
                  mt: '3px',
                }}
              />
              <Box sx={{ minWidth: 0 }}>
                <Typography component="span" sx={{ display: 'block', fontWeight: tiles ? 700 : 600, fontSize: tiles ? '1.25rem' : '1rem', overflowWrap: 'anywhere' }}>
                  {v.title}
                </Typography>
                {/* Коды — только при выборе позиции; на вопросе о характеристике это шум. */}
                {code && choosing && (
                  <Typography component="span" sx={{ display: 'block', fontFamily: MONO, fontSize: '0.8125rem', color: TOKENS.ink2, overflowWrap: 'anywhere' }}>
                    {code}
                  </Typography>
                )}
                {v.hint && (
                  <Typography component="span" sx={{ display: 'block', fontSize: '0.875rem', color: TOKENS.mute }}>
                    {v.hint}
                  </Typography>
                )}
                {/* Больше четырёх значений — «и ещё N»: вариант читается за секунду, а не абзацем. */}
                {v.reasons.map((reason) => (
                  <Typography key={reason.label} component="span" sx={{ display: 'block', fontSize: '0.875rem', overflowWrap: 'anywhere', color: reason.label === 'Противоречит' ? 'error.main' : TOKENS.ink2 }}>
                    {reason.label}: {reason.values.slice(0, 4).join('; ')}
                    {reason.values.length > 4 && <Box component="span" sx={{ color: TOKENS.mute }}>{` и ещё ${reason.values.length - 4}`}</Box>}
                  </Typography>
                ))}
                {v.effect && (
                  <Typography component="span" sx={{ display: tiles ? 'block' : { xs: 'block', sm: 'none' }, fontSize: '0.875rem', color: TOKENS.ink2 }}>
                    {v.effect}
                  </Typography>
                )}
              </Box>
              {!tiles && (
                <Typography component="span" sx={{ display: { xs: 'none', sm: 'block' }, fontSize: '0.875rem', color: TOKENS.ink2, whiteSpace: 'nowrap' }}>
                  {v.effect}
                </Typography>
              )}
            </Box>
          )
        })}
      </Box>
      {!choosing && preview.length > 1 && (
        <PositionsPreview
          positions={preview}
          asked={question.characteristic}
          keep={picked && picked.codes.length ? new Set(picked.codes) : null}
          answer={picked?.title ?? null}
          noun={stage === 'ktru' ? 'позиции КТРУ' : 'типы товара'}
        />
      )}
      <Box className="flex flex-wrap items-center gap-2.5">
        <Button type="submit" variant="contained" disabled={selected === null || busy} aria-keyshortcuts="Enter">
          <span>{busy ? 'Проверяем ответ…' : primary}</span>
          <Box component="span" sx={{ ml: 1.5, display: { xs: 'none', sm: 'inline-flex' } }}>
            <Kbd inverse>Enter</Kbd>
          </Box>
        </Button>
        <Button variant="outlined" disabled={busy} onClick={() => onAnswer(question.characteristic, NOTHING_FITS)}>
          Ни одна не подходит
        </Button>
        {onBack && (
          <Button color="inherit" disabled={busy} onClick={onBack} sx={{ color: TOKENS.ink2 }}>
            Вернуться к типу товара
          </Button>
        )}
      </Box>
    </Box>
  )
}

