import { Box, Typography } from '@mui/material'
import type { Span } from '../api/v1.ts'
import type { KeyboardEvent } from 'react'
import { highlightSegments } from '../pages/create/createModel.ts'
import { fromText, type FieldState } from '../pages/create/formModel.ts'
import { TOKENS } from '../theme'

type Props = {
  name: string
  description: string
  fields: FieldState[]
  /** Поле в фокусе: его фрагмент обводится. */
  active: string | null
  /** Клик по фрагменту переводит фокус на поле. */
  onPick?: (field: string) => void
  /** Куски описания, которые не легли ни в одно поле (`AutofillOut.unread`). */
  unread?: Span[]
}

/** Как прочитан фрагмент: в поле из текста, моделью (проверьте) или не разобран. Цвета — дополнительные из
 *  UI-кита портала: Green #0D9B68, Orange #F67319, Gray #7F8792. */
type Kind = 'text' | 'model' | 'unread'
const KIND: Record<Kind, { line: string; bg: string; title: string }> = {
  text: { line: '#0D9B68', bg: 'rgba(13, 155, 104, 0.10)', title: 'взято в поле' },
  model: { line: '#F67319', bg: 'rgba(246, 115, 25, 0.12)', title: 'модель взяла в поле, проверьте' },
  unread: { line: '#7F8792', bg: 'rgba(127, 135, 146, 0.12)', title: 'не разобрано: ни в одном поле' },
}
const UNREAD = '__unread__'

function kindOf(f: FieldState): Kind {
  return f.method === 'llm' && !f.checked && !f.confirmed ? 'model' : 'text'
}

function Marked({ text, spans, kinds, active, onPick }: { text: string; spans: { start: number; end: number; key: string }[]; kinds: Map<string, Kind>; active: string | null; onPick?: (field: string) => void }) {
  return (
    <>
      {highlightSegments(text, spans).map((s, i) =>
        s.mark && s.key?.startsWith(UNREAD) ? (
          <Box
            key={i}
            component="mark"
            title={KIND.unread.title}
            sx={{ background: KIND.unread.bg, boxShadow: `inset 0 -2px 0 ${KIND.unread.line}`, color: 'inherit', px: '1px' }}
          >
            {s.text}
          </Box>
        ) : s.mark && s.key ? (
          <Box
            key={i}
            component="mark"
            title={`${s.key}: ${KIND[kinds.get(s.key) ?? 'text'].title}, перейти к полю`}
            onClick={onPick ? () => onPick(s.key as string) : undefined}
            // Фрагмент ведёт к полю и с клавиатуры: Tab до него, Enter или пробел.
            role={onPick ? 'button' : undefined}
            tabIndex={onPick ? 0 : undefined}
            aria-label={onPick ? `${s.text}: поле «${s.key}»` : undefined}
            onKeyDown={
              onPick
                ? (e: KeyboardEvent) => {
                    if (e.key !== 'Enter' && e.key !== ' ') return
                    e.preventDefault()
                    onPick(s.key as string)
                  }
                : undefined
            }
            // Фрагмент из текста — синее подчёркивание с лёгкой подложкой: читается и на белом, и на голубом.
            sx={{
              background: KIND[kinds.get(s.key) ?? 'text'].bg,
              boxShadow: `inset 0 -2px 0 ${KIND[kinds.get(s.key) ?? 'text'].line}`,
              color: 'inherit',
              px: '1px',
              cursor: onPick ? 'pointer' : 'default',
              outline: s.key === active ? `2px solid ${TOKENS.acc}` : 'none',
              '&:focus-visible': { outline: `2px solid ${TOKENS.acc}`, outlineOffset: '1px' },
            }}
          >
            {s.text}
          </Box>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  )
}

/** Текст поставщика: цветом — что взято в поля (зелёный), что взяла модель (оранжевый, проверьте) и что не
 *  разобрано (серый); клик по взятому фрагменту ведёт к полю. */
export function SourceText({ name, description, fields, active, onPick, unread = [] }: Props) {
  const kinds = new Map(fields.map((f) => [f.name, kindOf(f)]))
  const spans = (field: 'name' | 'description') => [
    ...fields.flatMap((f) => (f.source && f.source.field === field && fromText(f) ? [{ start: f.source.start, end: f.source.end, key: f.name }] : [])),
    ...unread.filter((u) => u.field === field).map((u, i) => ({ start: u.start, end: u.end, key: `${UNREAD}${i}` })),
  ]
  const used = new Set([...kinds.entries()].filter(([n]) => fields.some((f) => f.name === n && f.source && fromText(f))).map(([, k]) => k))
  if (unread.length) used.add('unread')
  return (
    <Box className="flex flex-col gap-2">
      <Typography variant="subtitle1" component="p" sx={{ fontWeight: 600 }}>
        <Marked text={name} spans={spans('name')} kinds={kinds} active={active} onPick={onPick} />
      </Typography>
      {description && (
        <Typography variant="body2" component="p" sx={{ whiteSpace: 'pre-line' }}>
          <Marked text={description} spans={spans('description')} kinds={kinds} active={active} onPick={onPick} />
        </Typography>
      )}
      {used.size > 0 && (
        <Box component="ul" aria-label="Обозначения" className="flex flex-wrap gap-x-4 gap-y-1" sx={{ m: 0, p: 0, listStyle: 'none' }}>
          {(['text', 'model', 'unread'] as Kind[]).filter((k) => used.has(k)).map((k) => (
            <Typography component="li" key={k} sx={{ fontSize: '0.8125rem', color: TOKENS.ink2, display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <Box component="span" aria-hidden sx={{ width: 14, height: 10, background: KIND[k].bg, boxShadow: `inset 0 -2px 0 ${KIND[k].line}` }} />
              {KIND[k].title}
            </Typography>
          ))}
        </Box>
      )}
    </Box>
  )
}
