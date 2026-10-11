import { Box, Typography } from '@mui/material'
import { IconCheck, IconX } from '@tabler/icons-react'
import type { Position } from '../../../api/v1.ts'
import { MONO, TOKENS } from '../../../theme'

type Props = {
  /** Кандидаты в порядке модуля (позиции КТРУ на шаге 3, типы товара на шаге 2). */
  positions: Position[]
  /** О какой характеристике вопрос: её колонка первая. */
  asked: string
  /** Коды, которые останутся при выбранном ответе; null — ответ ещё не выбран. */
  keep: Set<string> | null
  /** Подпись выбранного ответа: «Ответ «Да» оставит 3 из 5». */
  answer: string | null
  noun: 'позиции КТРУ' | 'типы товара'
}

/** Сколько отличающих характеристик показать рядом с кодом: больше в строку не влезает. */
const MAX_COLUMNS = 3

const valuesOf = (p: Position, name: string): string => {
  const c = p.characteristics.find((x) => x.name === name)
  if (!c) return 'нет'
  if (c.values.length) return c.values.join(', ') + (c.unit ? ` ${c.unit}` : '')
  return 'любое'
}

/** Характеристики, по которым кандидаты различаются: спрошенная первой, дальше по порядку позиции. */
function differing(positions: Position[], asked: string): string[] {
  const names: string[] = []
  for (const p of positions) for (const c of p.characteristics) if (!names.includes(c.name)) names.push(c.name)
  const differs = names.filter((n) => new Set(positions.map((p) => valuesOf(p, n))).size > 1)
  return [asked, ...differs.filter((n) => n !== asked)].slice(0, MAX_COLUMNS)
}

/**
 * Какие кандидаты есть и чем различаются — таблицей под вопросом. Выбран ответ — у остающихся галочка, отсечённые
 * бледнеют и зачёркнуты: видно, какие позиции КТРУ уходят, а не только их число.
 */
export function PositionsPreview({ positions, asked, keep, answer, noun }: Props) {
  if (positions.length < 2) return null
  const columns = differing(positions, asked)
  const left = keep ? positions.filter((p) => keep.has(p.code)).length : null
  return (
    <Box component="section" aria-label={`Какие ${noun} остаются`} className="flex flex-col gap-2">
      <Typography sx={{ fontSize: '0.9375rem', fontWeight: 600 }}>
        {noun === 'позиции КТРУ' ? 'Позиции КТРУ' : 'Типы товара'}: {positions.length}
        {answer && left !== null && (
          <Box component="span" sx={{ fontWeight: 400, color: TOKENS.ink2 }}>
            {`. Ответ «${answer}» оставит ${left} из ${positions.length}: отмечены галочкой`}
          </Box>
        )}
      </Typography>
      <Box sx={{ overflowX: 'auto', border: `1px solid ${TOKENS.line}` }}>
        <Box component="table" sx={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
          <Box component="thead" sx={{ bgcolor: TOKENS.accSoft }}>
            <tr>
              <Box component="th" sx={{ width: 36 }}>
                <span className="sr-only">Останется</span>
              </Box>
              <Box component="th" sx={{ textAlign: 'left', fontWeight: 600, px: 1.5, py: 1, whiteSpace: 'nowrap' }}>
                Код
              </Box>
              {columns.map((c) => (
                <Box key={c} component="th" sx={{ textAlign: 'left', fontWeight: c === asked ? 700 : 600, px: 1.5, py: 1, color: c === asked ? TOKENS.acc : TOKENS.ink }}>
                  {c}
                </Box>
              ))}
            </tr>
          </Box>
          <tbody>
            {positions.map((p) => {
              const stays = keep ? keep.has(p.code) : null
              return (
                <Box
                  key={p.code}
                  component="tr"
                  sx={{
                    borderTop: `1px solid ${TOKENS.line}`,
                    opacity: stays === false ? 0.5 : 1,
                    bgcolor: stays ? 'rgba(13, 155, 104, 0.06)' : 'transparent',
                    transition: 'opacity 150ms ease-out, background-color 150ms ease-out',
                  }}
                >
                  <Box component="td" sx={{ textAlign: 'center', py: 1 }}>
                    {stays === true && <IconCheck size={18} stroke={2} color="#0b7f55" aria-label="останется" />}
                    {stays === false && <IconX size={16} stroke={2} color={TOKENS.mute} aria-label="отсечётся" />}
                  </Box>
                  <Box component="td" sx={{ px: 1.5, py: 1, fontFamily: MONO, fontSize: '0.8125rem', whiteSpace: 'nowrap', textDecoration: stays === false ? 'line-through' : 'none' }}>
                    {p.code}
                  </Box>
                  {columns.map((c) => (
                    <Box key={c} component="td" sx={{ px: 1.5, py: 1, color: c === asked ? TOKENS.ink : TOKENS.ink2, fontWeight: c === asked ? 600 : 400 }}>
                      {valuesOf(p, c)}
                    </Box>
                  ))}
                </Box>
              )
            })}
          </tbody>
        </Box>
      </Box>
    </Box>
  )
}
