import { Box, Button, ButtonBase, CircularProgress, Typography } from '@mui/material'
import { MONO, TOKENS } from '../../../theme'
import type { Segment } from './path.ts'
import type { Step } from './stepOf.ts'

type Props = {
  segments: Segment[]
  /** Пройденные шаги, на которые можно вернуться (2 и 3): ответить заново. */
  pickable: Step[]
  onPick: (step: Step) => void
}

const COLUMNS = 'minmax(0,1fr) minmax(0,1.3fr) minmax(0,1.15fr) minmax(0,0.85fr) minmax(0,0.7fr)'

const reached = (s: Segment) => s.state !== 'todo'

/**
 * Кружок шага из UI-кита портала (04_Steps): пройденный — красная заливка с белой цифрой, текущий — оранжевый
 * («вы здесь»), будущий — голубой с пунктирной серой рамкой. Пока модуль думает, цифру сменяет крутилка.
 */
function Dot({ s, size }: { s: Segment; size: number }) {
  const on = reached(s)
  const here = s.state === 'current' || s.state === 'busy'
  return (
    <Box
      aria-hidden
      sx={{
        width: size,
        height: size,
        flex: 'none',
        borderRadius: '50%',
        display: 'grid',
        placeItems: 'center',
        bgcolor: here ? TOKENS.now : on ? TOKENS.cta : TOKENS.accSoft,
        border: on ? 'none' : `1px dashed ${TOKENS.field}`,
        color: on ? '#ffffff' : TOKENS.mute,
        fontSize: size > 30 ? '1.25rem' : '0.8125rem',
        fontWeight: here ? 700 : 400,
        // Кольцо вокруг текущего: «вы здесь» видно и без цвета.
        boxShadow: here ? `0 0 0 4px #fde3d2` : 'none',
        lineHeight: 1,
      }}
    >
      {s.state === 'busy' ? (
        <CircularProgress
          size={size > 30 ? 20 : 12}
          thickness={5}
          sx={{ color: '#ffffff', '@media (prefers-reduced-motion: reduce)': { '& .MuiCircularProgress-svg': { animation: 'none' } } }}
        />
      ) : (
        s.step
      )}
    </Box>
  )
}

/**
 * Линия от кружка к следующему: красная между пройденными; от текущего — оранжевая точка и пунктир дальше, как в ките.
 */
function Link({ from, to }: { from: Segment; to: Segment | undefined }) {
  if (!to) return null
  const solid = reached(from) && reached(to) && from.state !== 'current' && from.state !== 'busy'
  const head = from.state === 'current' || from.state === 'busy'
  return (
    <Box aria-hidden sx={{ flex: 1, minWidth: 16, display: 'flex', alignItems: 'center', mx: 1 }}>
      {solid ? (
        <Box sx={{ flex: 1, height: '1px', bgcolor: TOKENS.cta }} />
      ) : (
        <>
          {head && (
            <>
              <Box sx={{ width: 20, height: '1px', bgcolor: TOKENS.now }} />
              <Box sx={{ width: 5, height: 5, borderRadius: '50%', bgcolor: TOKENS.now, flex: 'none' }} />
            </>
          )}
          <Box sx={{ flex: 1, borderTop: `1px dashed ${TOKENS.field}` }} />
        </>
      )}
    </Box>
  )
}

function Text({ s, pick }: { s: Segment; pick: boolean }) {
  const active = s.state === 'current' || s.state === 'busy'
  return (
    <>
      <Typography
        component="span"
        sx={{ display: 'block', fontSize: '0.8125rem', lineHeight: '1rem', color: active || s.state === 'done' ? TOKENS.ink : TOKENS.mute, fontWeight: active ? 700 : 400 }}
      >
        {s.label}
      </Typography>
      {s.value && (
        <Typography
          component="span"
          sx={{
            display: 'block',
            mt: '2px',
            fontSize: '0.9375rem',
            lineHeight: 1.35,
            fontWeight: s.state === 'todo' ? 400 : 600,
            color: s.state === 'todo' ? TOKENS.mute : TOKENS.ink,
            overflowWrap: 'anywhere',
          }}
        >
          {s.value}
        </Typography>
      )}
      {s.code && (
        <Typography component="span" sx={{ display: 'block', fontFamily: MONO, fontSize: '0.8125rem', color: TOKENS.ink2, overflowWrap: 'anywhere' }}>
          {s.code}
        </Typography>
      )}
      {/* Пройденный шаг открывается: видно и без наведения. */}
      {pick && (
        <Box component="span" aria-hidden className="pick" sx={{ display: 'inline-block', mt: '2px', fontSize: '0.8125rem', color: TOKENS.acc }}>
          Изменить
        </Box>
      )}
    </>
  )
}

/** Полоса шагов: у каждого шага его результат с кодом; уже 900 px — одна строка «Шаг N из 5». */
export function WizardPath({ segments, pickable, onPick }: Props) {
  const current = segments.find((s) => s.state === 'current' || s.state === 'busy') ?? segments[0]
  const passed = segments.filter((s) => s.state === 'done' && pickable.includes(s.step))
  return (
    <Box component="nav" aria-label="Шаги создания СТЕ">
      <Box
        component="ol"
        sx={{ display: { xs: 'none', md: 'grid' }, gridTemplateColumns: COLUMNS, columnGap: 0, m: 0, p: 0, listStyle: 'none' }}
      >
        {segments.map((s, i) => {
          const active = s.state === 'current' || s.state === 'busy'
          const canPick = s.state === 'done' && pickable.includes(s.step)
          const body = (
            <>
              <Box sx={{ display: 'flex', alignItems: 'center', width: '100%' }}>
                <Dot s={s} size={40} />
                <Link from={s} to={segments[i + 1]} />
              </Box>
              <Box sx={{ mt: 1, pr: 2 }}>
                <Text s={s} pick={canPick} />
              </Box>
            </>
          )
          // Колонка flex сверху вниз: у <button> с display: block содержимое центруется по вертикали, и кружок
          // короткой колонки съезжает вниз относительно соседей.
          const box = {
            display: 'flex',
            flexDirection: 'column' as const,
            alignItems: 'stretch',
            justifyContent: 'flex-start',
            width: '100%',
            height: '100%',
            textAlign: 'left' as const,
            pb: 0.5,
          }
          return (
            <Box component="li" key={s.step} aria-current={active ? 'step' : undefined} sx={{ minWidth: 0 }}>
              {canPick ? (
                <ButtonBase
                  onClick={() => onPick(s.step)}
                  aria-label={`${s.label}: ${s.value}${s.code ? ` ${s.code}` : ''}. Изменить`}
                  sx={{
                    ...box,
                    '&:hover .pick': { textDecoration: 'underline' },
                    '&.Mui-focusVisible': { outline: `2px solid ${TOKENS.acc}`, outlineOffset: 2 },
                  }}
                >
                  {body}
                </ButtonBase>
              ) : (
                <Box sx={box}>{body}</Box>
              )}
            </Box>
          )
        })}
      </Box>
      <Box sx={{ display: { xs: 'block', md: 'none' } }}>
        <Box aria-hidden sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
          {segments.map((s, i) => (
            <Box key={s.step} sx={{ display: 'flex', alignItems: 'center', flex: i < segments.length - 1 ? 1 : 'none' }}>
              <Dot s={s} size={24} />
              <Link from={s} to={segments[i + 1]} />
            </Box>
          ))}
        </Box>
        <Typography sx={{ fontSize: '0.8125rem', color: TOKENS.ink, fontWeight: 700 }}>
          Шаг {current.step} из 5. {current.label}
        </Typography>
        {current.value && <Typography sx={{ fontSize: '0.9375rem', fontWeight: 600, overflowWrap: 'anywhere' }}>{current.value}</Typography>}
        {/* На узком экране текста шагов нет: пройденные шаги открываются отсюда. */}
        {passed.length > 0 && (
          <Box className="mt-1 flex flex-wrap items-center gap-x-1" sx={{ fontSize: '0.875rem', color: TOKENS.ink2 }}>
            <span>Изменить:</span>
            {passed.map((s) => (
              <Button
                key={s.step}
                size="small"
                onClick={() => onPick(s.step)}
                aria-label={`${s.label}: ${s.value}${s.code ? ` ${s.code}` : ''}. Изменить`}
                sx={{ minWidth: 0, px: 0.75 }}
              >
                {s.label}
              </Button>
            ))}
          </Box>
        )}
      </Box>
    </Box>
  )
}
