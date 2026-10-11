import '@fontsource-variable/open-sans'
import { Box, Typography } from '@mui/material'
import { useId, useRef, useState, type KeyboardEvent } from 'react'
import { TOKENS } from '../../../theme'
import type { PreviewPhoto, PreviewView } from './portalPreview.ts'

// Цвета и шрифт страницы СТЕ портала (сняты с макета preview-and-files.html): только для превью,
// в токены приложения не попадают (DESIGN.md, исключение предпросмотра).
const P = {
  blue: '#264b82',
  band: '#e8eef6',
  text: '#1a1a1a',
  muted: '#5b6577',
  head: '#6d7684',
  line: '#cdd6e3',
  rowLine: '#edf0f5',
  dash: '#b9c3d2',
  sep: '#8a93a1',
  todo: '#a14f00',
}
const FONT = "'Open Sans Variable', 'Open Sans', sans-serif"

type Tab = 'general' | 'chars' | 'extra'
const TABS: { key: Tab; label: string }[] = [
  { key: 'general', label: 'Общие сведения' },
  { key: 'chars', label: 'Характеристики' },
  { key: 'extra', label: 'Доп. сведения' },
]

function Photo({ photo }: { photo: PreviewPhoto }) {
  // Своё фото не загрузилось — картинка категории (как у портала без фото), потом заглушка.
  const [failed, setFailed] = useState(0)
  const chain = [photo.src, photo.fallback].filter((s): s is string => s !== null)
  const src = chain[failed] ?? null
  const shown = src !== null
  const caption = failed > 0 ? 'картинка категории, пока нет вашего фото' : photo.caption
  return (
    <Box>
      <Box
        sx={{
          height: { xs: 96, sm: 130 },
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          textAlign: 'center',
          border: shown ? `1px solid ${P.rowLine}` : `1px dashed ${P.dash}`,
          borderRadius: '4px',
          overflow: 'hidden',
          color: P.muted,
          fontSize: 11,
          p: shown ? 0 : 1,
        }}
      >
        {shown ? (
          // Портал не ответил или картинки нет — вместо битой иконки заглушка (onError).
          <Box
            component="img"
            key={src}
            src={src}
            alt={photo.kind === 'own' && failed === 0 ? 'Фото товара' : 'Картинка категории'}
            width={300}
            height={300}
            onError={() => setFailed((n) => n + 1)}
            sx={{ width: 'auto', height: 'auto', maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
          />
        ) : (
          'Фото нет'
        )}
      </Box>
      {shown && caption && <Box sx={{ mt: 0.5, fontSize: 11, color: P.muted, lineHeight: 1.35 }}>{caption}</Box>}
    </Box>
  )
}

function Rows({ head, rows }: { head: [string, string]; rows: { key: string; name: string; value: string }[] }) {
  return (
    <Box component="table" sx={{ width: '100%', borderCollapse: 'collapse', mt: 1.75, tableLayout: 'fixed' }}>
      <thead>
        <tr>
          {head.map((h, i) => (
            <Box
              component="th"
              key={h}
              sx={{
                width: i === 0 ? '45%' : undefined,
                textAlign: 'left',
                fontSize: 11,
                fontWeight: 700,
                color: P.head,
                textTransform: 'uppercase',
                py: 0.75,
                pr: 1,
                borderBottom: `2px solid ${P.band}`,
              }}
            >
              {h}
            </Box>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <Box component="td" sx={{ color: P.muted, py: 0.75, pr: 1, borderBottom: `1px solid ${P.rowLine}`, overflowWrap: 'break-word', verticalAlign: 'top' }}>
              {r.name}
            </Box>
            <Box component="td" sx={{ py: 0.75, borderBottom: `1px solid ${P.rowLine}`, overflowWrap: 'break-word', verticalAlign: 'top' }}>
              {r.value}
            </Box>
          </tr>
        ))}
      </tbody>
    </Box>
  )
}

/** Карточка в разметке страницы СТЕ портала: крошки, вкладки, фото и сведения, таблица характеристик. */
export function PortalPreview({ view }: { view: PreviewView }) {
  const id = useId()
  const [tab, setTab] = useState<Tab>('chars')
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({})
  // Вкладки как у вкладок MUI на шаге 5: стрелки, Home и End переключают и переводят фокус.
  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const at = TABS.findIndex((t) => t.key === tab)
    const next =
      e.key === 'ArrowRight' ? (at + 1) % TABS.length : e.key === 'ArrowLeft' ? (at - 1 + TABS.length) % TABS.length : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : null
    if (next === null) return
    e.preventDefault()
    setTab(TABS[next].key)
    tabRefs.current[TABS[next].key]?.focus()
  }
  const facts = view.facts.slice(1)
  const panels: Record<Tab, { head: [string, string]; rows: { key: string; name: string; value: string }[] }> = {
    general: {
      head: ['Сведение', 'Значение'],
      rows: [
        { key: 'name', name: 'Наименование', value: view.title },
        ...(view.crumbs.length ? [{ key: 'cat', name: 'Категория', value: view.crumbs[view.crumbs.length - 1] }] : []),
        ...facts.map((f) => ({ key: f.label, name: f.label, value: f.value })),
      ],
    },
    chars: {
      head: ['Характеристика', 'Значение'],
      rows: view.rows.map((r) => ({ key: r.name, name: r.name, value: r.unit ? `${r.value} ${r.unit}` : r.value })),
    },
    extra: { head: ['Сведение', 'Значение'], rows: view.extra.map((e) => ({ key: e.label, name: e.label, value: e.value })) },
  }
  return (
    <Box>
      <Typography sx={{ mb: 1, fontSize: 13, color: TOKENS.ink2 }}>Так карточку увидят заказчики после модерации. Цена добавляется на портале.</Typography>
      <Box sx={{ fontFamily: FONT, fontSize: 13, lineHeight: 1.5, color: P.text, bgcolor: '#ffffff', border: `1px solid ${P.line}`, borderRadius: '6px', overflow: 'hidden' }}>
        <Box sx={{ px: { xs: 1.5, sm: 2.25 }, pt: 1.75, pb: 2.25 }}>
          {view.crumbs.length > 0 && (
            <Box sx={{ color: P.blue, fontSize: 12, overflowWrap: 'anywhere' }}>
              {view.crumbs.map((c, i) => (
                <span key={`${i}-${c}`}>
                  {i > 0 && (
                    <Box component="span" aria-hidden sx={{ color: P.sep, mx: 0.5 }}>
                      ›
                    </Box>
                  )}
                  {c}
                </span>
              ))}
            </Box>
          )}
          <Box
            role="tablist"
            aria-label="Разделы страницы СТЕ"
            sx={{ display: 'flex', gap: { xs: 1.5, sm: 3.25 }, bgcolor: P.band, px: { xs: 1.5, sm: 2.25 }, mx: { xs: -1.5, sm: -2.25 }, mt: 1.25, mb: 1.75, overflowX: 'auto' }}
          >
            {TABS.map((t) => {
              const on = tab === t.key
              return (
                <Box
                  key={t.key}
                  component="button"
                  type="button"
                  role="tab"
                  id={`${id}-${t.key}`}
                  ref={(el: HTMLButtonElement | null) => {
                    tabRefs.current[t.key] = el
                  }}
                  aria-selected={on}
                  aria-controls={`${id}-panel`}
                  tabIndex={on ? 0 : -1}
                  onKeyDown={onTabKey}
                  onClick={() => setTab(t.key)}
                  sx={{
                    flex: 'none',
                    font: 'inherit',
                    fontSize: 11,
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    // Неактивная вкладка темнее, чем на портале (#6d7684 на полосе — 3,9:1): нужно 4,5:1.
                    color: on ? P.blue : P.muted,
                    bgcolor: 'transparent',
                    border: 0,
                    borderBottom: `3px solid ${on ? P.blue : 'transparent'}`,
                    py: 1.25,
                    px: 0,
                    cursor: 'pointer',
                    '&:focus-visible': { outline: `2px solid ${P.blue}`, outlineOffset: '2px' },
                  }}
                >
                  {t.label}
                </Box>
              )
            })}
          </Box>
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '96px minmax(0,1fr)', sm: '150px minmax(0,1fr)' }, gap: { xs: 1.5, sm: 2.25 } }}>
            <Photo key={`${view.photo.src}|${view.photo.fallback}`} photo={view.photo} />
            <Box sx={{ minWidth: 0 }}>
              <Box component="h3" sx={{ m: 0, mb: 1, fontSize: { xs: 15, sm: 17 }, fontWeight: 700, lineHeight: 1.3, overflowWrap: 'anywhere' }}>
                {view.title}
              </Box>
              {view.facts.map((f) => (
                <Box key={f.label} sx={{ mt: 0.5, color: P.muted, overflowWrap: 'anywhere' }}>
                  {f.label}:{' '}
                  <Box component="span" sx={{ color: f.missing ? P.todo : P.text, fontWeight: f.missing ? 400 : 600 }}>
                    {f.value}
                  </Box>
                </Box>
              ))}
            </Box>
          </Box>
          <Box role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${tab}`}>
            {tab === 'chars' && !view.rows.length ? (
              <Box sx={{ mt: 1.75, color: P.muted }}>Характеристики ещё не заполнены.</Box>
            ) : (
              <Rows head={panels[tab].head} rows={panels[tab].rows} />
            )}
          </Box>
        </Box>
      </Box>
    </Box>
  )
}
