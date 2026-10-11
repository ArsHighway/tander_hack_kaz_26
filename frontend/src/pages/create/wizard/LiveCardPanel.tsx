import { IconEye, IconPhoto, IconX } from '@tabler/icons-react'
import { Box, Button, Drawer, IconButton, Tab, Tabs, Typography } from '@mui/material'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { photoUrl } from '../../../api/v1.ts'
import { MONO, TOKENS } from '../../../theme'
import { OriginMark } from '../../../card/OriginMark'
import { changedRows, type LiveCardView, type LiveRow } from './liveCard.ts'
import { readinessText } from './groups.ts'
import { KtruTrail } from './KtruTrail'

/** Изменившаяся строка подсвечивается столько, чтобы глаз успел её найти. */
const FLASH_MS = 1500

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Путь категории: больше трёх звеньев — первые сворачиваются в «…», полный путь в подсказке. */
function Crumbs({ path }: { path: string[] }) {
  if (!path.length) return null
  const shown = path.length > 3 ? ['…', ...path.slice(-3)] : path
  return (
    <Typography title={path.join(' › ')} sx={{ fontSize: '0.8125rem', color: TOKENS.mute, overflowWrap: 'anywhere' }}>
      {shown.join(' › ')}
    </Typography>
  )
}

const WAITING = (
  <Box component="span" sx={{ color: TOKENS.acc, fontWeight: 500 }}>
    ждёт ответа
  </Box>
)

function Codes({ view }: { view: LiveCardView }) {
  const { kpgz, okpd2, ktru } = view.codes
  const code = (v: string) => (
    <Box component="span" sx={{ fontFamily: MONO }}>
      {v}
    </Box>
  )
  // Категория не найдена: вопроса нет, «ждёт ответа» было бы неправдой.
  const absent = view.notFound ? <Box component="span" sx={{ color: TOKENS.mute }}>нет</Box> : view.category ? null : WAITING
  const missing = ktru.kind === 'missing' ? <Box component="span" sx={{ color: TOKENS.ink2 }}>{ktru.text}</Box> : null
  const rows: [string, ReactNode][] = [
    ['КТРУ', ktru.kind === 'code' ? code(ktru.code) : ktru.kind === 'pending' ? WAITING : missing],
    ['ОКПД2', okpd2 ? code(okpd2) : absent],
    ['КПГЗ', kpgz ? code(kpgz) : absent],
  ]
  return (
    <Box component="dl" sx={{ m: 0, display: 'grid', gridTemplateColumns: 'max-content minmax(0,1fr)', columnGap: 1.5, rowGap: 0.5, fontSize: '0.875rem' }}>
      {rows
        .filter(([, v]) => v)
        .map(([k, v]) => (
          <Box key={k} sx={{ display: 'contents' }}>
            <Box component="dt" sx={{ color: TOKENS.mute }}>
              {k}
            </Box>
            <Box component="dd" sx={{ m: 0, overflowWrap: 'anywhere' }}>
              {v}
            </Box>
          </Box>
        ))}
    </Box>
  )
}

/** Фото квадратом слева от наименования, как на странице СТЕ портала; без фото — значок и подпись. */
function Photo({ photoId, size = 112 }: { photoId: number | null; size?: number }) {
  const [broken, setBroken] = useState<number | null>(null)
  const shown = photoId !== null && broken !== photoId
  return (
    <Box sx={{ width: size, height: size, flex: 'none', bgcolor: '#ffffff', display: 'grid', placeItems: 'center', overflow: 'hidden' }}>
      {shown ? (
        <Box
          component="img"
          src={photoUrl(photoId)}
          alt="Фото товара"
          decoding="async"
          onError={() => setBroken(photoId)}
          // Пиксель вместо фото (тестовые заявки) — не фото товара: показываем «Фото нет».
          onLoad={(e) => {
            const img = e.currentTarget as HTMLImageElement
            if (img.naturalWidth < 16 || img.naturalHeight < 16) setBroken(photoId)
          }}
          sx={{ maxWidth: '100%', maxHeight: size, objectFit: 'contain' }}
        />
      ) : (
        <Box className="flex flex-col items-center gap-1 px-2 text-center" sx={{ color: TOKENS.mute, fontSize: '0.75rem', lineHeight: 1.2 }}>
          <IconPhoto size={30} stroke={1.25} aria-hidden />
          Фото нет
        </Box>
      )}
    </Box>
  )
}

function RowValue({ row, asking }: { row: LiveRow; asking: string }) {
  if (row.state === 'asking') {
    return (
      <Box component="span" sx={{ color: TOKENS.acc, fontWeight: 500 }}>
        {asking}
      </Box>
    )
  }
  if (!row.value) return <Box component="span" sx={{ color: TOKENS.mute }}>не указано</Box>
  return (
    <Box component="span" sx={{ fontWeight: 600, color: TOKENS.ink }}>
      {row.value}
      {row.unit ? ` ${row.unit}` : ''}
    </Box>
  )
}

type CardProps = {
  view: LiveCardView
  /** Новый ответ модуля: после него изменившиеся строки подсвечиваются (правка поля — нет). */
  pulse: unknown
  /** Вкладка «Ваш текст». */
  source: ReactNode
  /** Над вкладками: «Осталось заполнить» на шаге 4. */
  top?: ReactNode
  /** В нижней шторке вопрос не справа, а под ней. */
  sheet?: boolean
  /** «Открыть как на портале»: предпросмотр страницы СТЕ. */
  onPreview?: () => void
  /** Фото товара из шага 4. */
  photoId?: number | null
  /** Как сузили выбор — на шагах 4 и 5; на шагах вопросов это видно рядом с вариантами. */
  showTrail?: boolean
}

/**
 * Карточка товара отдельной панелью, как выбранная СТЕ в UI-ките портала: голубой блок с наименованием, фото и
 * кодами; ниже — характеристики строкой «имя: значение» и вторая вкладка с текстом поставщика.
 */
export function LiveCard({ view, pulse, source, top, sheet = false, onPreview, photoId = null, showTrail = false }: CardProps) {
  const id = useId()
  const [tab, setTab] = useState<'card' | 'text'>('card')
  const [flash, setFlash] = useState<Set<string>>(() => new Set())
  const last = useRef<LiveCardView | null>(null)
  const lastPulse = useRef(pulse)
  // Таймер живёт до следующего ответа модуля: правка поля меняет вид карточки, но подсветку не продлевает.
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => {
    const before = last.current
    last.current = view
    if (pulse === lastPulse.current) return
    lastPulse.current = pulse
    const changed = changedRows(before, view)
    window.clearTimeout(timer.current)
    if (!changed.size || reducedMotion()) {
      setFlash(new Set())
      return
    }
    setFlash(changed)
    timer.current = window.setTimeout(() => setFlash(new Set()), FLASH_MS)
  }, [view, pulse])
  useEffect(() => () => window.clearTimeout(timer.current), [])

  const { done, total } = view.ready
  return (
    <Box className="flex flex-col gap-4">
      {onPreview && (
        <Button variant="outlined" fullWidth startIcon={<IconEye size={20} stroke={1.5} aria-hidden />} onClick={onPreview}>
          Открыть как на портале
        </Button>
      )}
      <Box className="flex flex-col gap-3 p-4" sx={{ bgcolor: TOKENS.accSoft }}>
        <Box className="flex items-start gap-3">
          {!sheet && <Photo photoId={photoId} />}
          <Box className="flex min-w-0 flex-col gap-1">
            <Crumbs path={view.path} />
            <Typography sx={{ fontSize: '1.0625rem', fontWeight: 600, lineHeight: 1.35, color: TOKENS.acc, overflowWrap: 'anywhere' }}>{view.name}</Typography>
          </Box>
        </Box>
        <Codes view={view} />
        {total > 0 && (
          <Box>
            <Typography sx={{ fontSize: '0.875rem', fontWeight: 600 }}>{readinessText(view.ready)}</Typography>
            <Box aria-hidden sx={{ mt: 0.75, height: 4, bgcolor: '#ffffff', overflow: 'hidden' }}>
              <Box sx={{ height: '100%', width: `${(100 * done) / total}%`, bgcolor: TOKENS.done }} />
            </Box>
          </Box>
        )}
      </Box>
      {top}
      <Box>
        <Tabs
          value={tab}
          onChange={(_, v: 'card' | 'text') => setTab(v)}
          aria-label="Характеристики и ваш текст"
          sx={{ minHeight: 40, '@media (pointer: coarse)': { minHeight: 44 }, borderBottom: `1px solid ${TOKENS.line}` }}
        >
          <Tab value="card" label="Характеристики" id={`${id}-card`} aria-controls={`${id}-card-panel`} sx={{ minHeight: 40, '@media (pointer: coarse)': { minHeight: 44 }, px: 1.5, minWidth: 0 }} />
          <Tab value="text" label="Ваш текст" id={`${id}-text`} aria-controls={`${id}-text-panel`} sx={{ minHeight: 40, '@media (pointer: coarse)': { minHeight: 44 }, px: 1.5, minWidth: 0 }} />
        </Tabs>
        <Box role="tabpanel" id={`${id}-card-panel`} aria-labelledby={`${id}-card`} hidden={tab !== 'card'} sx={{ pt: 1.5 }}>
          {tab === 'card' && (
            <Box className="flex flex-col gap-3">
              {!view.category && (
                <Typography sx={{ fontSize: '0.875rem', color: TOKENS.mute }}>
                  {view.notFound ? 'Категория не найдена: измените описание на шаге 1.' : 'Характеристики появятся, когда будет выбран тип товара.'}
                </Typography>
              )}
              {view.rows.length > 0 && (
                <Box component="dl" sx={{ m: 0 }}>
                  {view.rows.map((r) => (
                    <Box
                      key={r.name}
                      sx={{
                        display: 'grid',
                        gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)',
                        columnGap: 2,
                        py: 1,
                        px: 0.5,
                        fontSize: '0.9375rem',
                        borderBottom: `1px solid ${TOKENS.line}`,
                        bgcolor: flash.has(r.name) ? TOKENS.accSoft : 'transparent',
                        transition: 'background-color 400ms ease-out',
                      }}
                    >
                      <Box component="dt" sx={{ color: TOKENS.mute, overflowWrap: 'anywhere' }}>
                        {r.name}
                        {r.origin && <OriginMark origin={r.origin} />}
                      </Box>
                      <Box component="dd" sx={{ m: 0, overflowWrap: 'anywhere' }}>
                        <RowValue row={r} asking={sheet ? 'ответьте на вопрос' : 'выберите справа'} />
                      </Box>
                    </Box>
                  ))}
                </Box>
              )}
              {showTrail && <KtruTrail trail={view.trail ?? []} />}
            </Box>
          )}
        </Box>
        <Box role="tabpanel" id={`${id}-text-panel`} aria-labelledby={`${id}-text`} hidden={tab !== 'text'} sx={{ pt: 1.5 }}>
          {tab === 'text' && source}
        </Box>
      </Box>
    </Box>
  )
}

/** Шаг 1: карточки ещё нет — её место и что будет дальше. */
export function CardPlaceholder({ steps }: { steps: [number, string][] }) {
  return (
    <Box className="flex flex-col gap-4">
      <Box className="flex flex-col gap-3 p-4" sx={{ bgcolor: TOKENS.accSoft }}>
        <Box className="flex items-start gap-3">
          <Photo photoId={null} />
          <Box className="flex min-w-0 flex-col gap-1">
            <Typography sx={{ fontSize: '1.0625rem', fontWeight: 600, color: TOKENS.acc }}>Новая СТЕ</Typography>
            <Typography sx={{ fontSize: '0.875rem', color: TOKENS.ink2 }}>Карточка заполнится по вашему описанию: коды, характеристики и откуда взято каждое значение.</Typography>
          </Box>
        </Box>
      </Box>
      <Box component="section" aria-labelledby="describe-next">
        <Typography id="describe-next" component="h2" sx={{ fontWeight: 700, fontSize: '1rem', mb: 1.5 }}>
          Что будет дальше
        </Typography>
        <Box component="ol" sx={{ m: 0, p: 0, listStyle: 'none', display: 'grid', gap: 1.5 }}>
          {steps.map(([n, line]) => (
            <Box component="li" key={n} sx={{ display: 'grid', gridTemplateColumns: '24px minmax(0,1fr)', gap: 1.25, color: TOKENS.ink2, fontSize: '0.9375rem' }}>
              <Box
                component="span"
                aria-hidden
                sx={{ width: 24, height: 24, borderRadius: '50%', bgcolor: TOKENS.accSoft, border: `1px dashed ${TOKENS.field}`, display: 'grid', placeItems: 'center', fontSize: '0.8125rem', color: TOKENS.mute }}
              >
                {n}
              </Box>
              <span>{line}</span>
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  )
}

type SheetProps = {
  view: LiveCardView
  open: boolean
  onOpen: () => void
  onClose: () => void
  /** Полоса внизу экрана; на шаге 4 её заменяет кнопка в полосе шага. */
  bar: boolean
  /** Шторка закрылась совсем: можно переводить фокус на поле. */
  onExited?: () => void
  children: ReactNode
}

/** Уже 900 px: карточка — нижняя шторка, внизу экрана полоса «Карточка: N из M · Открыть». */
export function CardSheet({ view, open, onOpen, onClose, bar, onExited, children }: SheetProps) {
  const { done, total } = view.ready
  return (
    <>
      {bar && (
        <Box
          sx={{
            display: { xs: 'flex', md: 'none' },
            position: 'fixed',
            left: 0,
            right: 0,
            bottom: 0,
            zIndex: 1100,
            alignItems: 'center',
            gap: 1.5,
            px: 2,
            py: 1,
            bgcolor: 'background.paper',
            borderTop: `1px solid ${TOKENS.line}`,
            boxShadow: '0 -6px 8px -8px rgba(38,75,130,0.2)',
          }}
        >
          <Typography sx={{ fontSize: '0.9375rem', fontWeight: 600 }}>{total > 0 ? `КТРУ: заполнено ${done} из ${total}` : 'Карточка'}</Typography>
          <Button variant="outlined" size="small" onClick={onOpen} sx={{ ml: 'auto' }}>
            Открыть
          </Button>
        </Box>
      )}
      <Drawer
        anchor="bottom"
        open={open}
        onClose={onClose}
        slotProps={{
          paper: {
            role: 'dialog',
            'aria-modal': true,
            'aria-label': 'Карточка',
            sx: { maxHeight: '85dvh', overscrollBehavior: 'contain' },
          },
          transition: { onExited },
        }}
      >
        {/* Крестик в углу шторки; ниже — карточка и вкладки «Характеристики» и «Ваш текст». */}
        <Box sx={{ position: 'relative', px: 2, pt: 6, pb: 3 }}>
          <IconButton aria-label="Закрыть" onClick={onClose} sx={{ position: 'absolute', right: 8, top: 4, zIndex: 1, color: TOKENS.ink2 }}>
            <IconX size={20} stroke={1.5} aria-hidden />
          </IconButton>
          {children}
        </Box>
      </Drawer>
    </>
  )
}
