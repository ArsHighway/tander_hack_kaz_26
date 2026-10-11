import { Alert, Box, Button, Paper, Skeleton, Tab, Tabs, Typography } from '@mui/material'
import { IconDownload, IconFileSpreadsheet } from '@tabler/icons-react'
import { useEffect, useState } from 'react'
import { ApiError, api, type BatchState, type Submission } from '../../api/v1.ts'
import { NoPhoto, PhotoThumb } from '../../card/PhotoThumb'
import { NBSP, plural } from '../../lib/format'
import { go, navigate } from '../../lib/router'
import { Page } from '../../shell/Page'
import { StatusMark, type StatusTone } from '../../shell/StatusMark'
import { TOKENS } from '../../theme'
import { STATUS_TEXT } from '../batch/batchView.ts'
import { PREFILL_KEY, revisePrefill } from '../create/createModel.ts'
import { codesText } from '../create/formModel.ts'

// Статус глазами поставщика: кружок и капс, как в реестрах портала.
const STATUS: Record<Submission['status'], { text: string; tone: StatusTone }> = {
  pending: { text: 'на модерации', tone: 'wait' },
  accepted: { text: 'принята', tone: 'ok' },
  returned: { text: 'возвращена', tone: 'back' },
}

const BATCH_STATUS: Record<BatchState['status'], { text: string; tone: StatusTone }> = {
  queued: { text: 'в очереди', tone: 'off' },
  running: { text: 'обрабатывается', tone: 'info' },
  done: { text: 'готов', tone: 'ok' },
  cancelled: { text: 'прерван', tone: 'off' },
  failed: { text: 'сбой', tone: 'back' },
}

const time = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/** Возвращённая заявка открывается в форме «Создание СТЕ» с её текстом, характеристиками и замечаниями. */
function revise(s: Submission) {
  try {
    sessionStorage.setItem(PREFILL_KEY, JSON.stringify(revisePrefill(s)))
  } catch {
    return
  }
  go('/')
}

function SubmissionItem({ s, first }: { s: Submission; first: boolean }) {
  const fixed = s.status === 'pending' && Boolean(s.decision_note)
  return (
    <Box component="li" className="flex gap-4 px-5 py-4 md:px-6" sx={{ borderTop: first ? 0 : 1, borderColor: 'divider' }}>
      {s.card.photo_id != null ? <PhotoThumb photoId={s.card.photo_id} size={56} /> : <NoPhoto size={56} />}
      <Box className="flex min-w-0 flex-1 flex-col gap-2">
        <Box className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <Typography sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>{s.card.name}</Typography>
          <StatusMark tone={STATUS[s.status].tone}>{fixed ? 'исправлена, на модерации' : STATUS[s.status].text}</StatusMark>
        </Box>
        <Typography variant="body2" color="text.secondary">
          №{NBSP}
          {s.id}, {time.format(new Date(s.created_at))}, {codesText(s.card)}
          {s.batch ? `, строка ${s.batch.row} файла` : ''}
        </Typography>
        {s.status === 'returned' && (
          <Box className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
            <Typography variant="body2" className="whitespace-pre-line">
              {s.decision_note || 'Модератор вернул заявку без текста.'}
            </Typography>
            <Button variant="outlined" size="small" className="shrink-0" onClick={() => revise(s)}>
              Исправить
            </Button>
          </Box>
        )}
      </Box>
    </Box>
  )
}

function Empty({ title, text, action }: { title: string; text: string; action: { label: string; href: string } }) {
  return (
    <Paper variant="outlined" className="p-8 text-center">
      <Typography variant="h6" component="h2">
        {title}
      </Typography>
      <Typography color="text.secondary" className="mt-1">
        {text}
      </Typography>
      <Button variant="contained" className="mt-4" onClick={() => go(action.href)}>
        {action.label}
      </Button>
    </Paper>
  )
}

/** Строк по статусам словами: только ненулевые («готово: 12, нужно исправить: 3»). */
const countsText = (b: BatchState): string =>
  (Object.keys(STATUS_TEXT) as (keyof typeof STATUS_TEXT)[])
    .filter((k) => (b.counts?.[k] ?? 0) > 0)
    .map((k) => `${STATUS_TEXT[k]}: ${b.counts?.[k]}`)
    .join(', ')

type Group = { jobId: string; filename: string; batch: BatchState | null; subs: Submission[] }

/** Пакеты (живые, новые сверху) и заявки из их строк; заявки истёкшего пакета — своей группой ниже. */
function groupsOf(batches: BatchState[], subs: Submission[]): Group[] {
  const groups = new Map<string, Group>()
  for (const b of batches) groups.set(b.job_id, { jobId: b.job_id, filename: b.filename, batch: b, subs: [] })
  for (const s of subs) {
    if (!s.batch) continue
    const g = groups.get(s.batch.job_id) ?? { jobId: s.batch.job_id, filename: s.batch.filename, batch: null, subs: [] }
    g.subs.push(s)
    groups.set(s.batch.job_id, g)
  }
  return [...groups.values()]
}

function BatchGroup({ g }: { g: Group }) {
  const b = g.batch
  const href = `/batch?job=${encodeURIComponent(g.jobId)}`
  return (
    <Paper variant="outlined" className="overflow-hidden">
      <Box className="flex flex-wrap items-start gap-4 px-5 py-4 md:px-6">
        <Box aria-hidden sx={{ width: 56, height: 56, flex: 'none', bgcolor: TOKENS.accSoft, color: TOKENS.acc, display: 'grid', placeItems: 'center', borderRadius: '6px' }}>
          <IconFileSpreadsheet size={28} stroke={1.5} />
        </Box>
        <Box className="flex min-w-0 flex-1 flex-col gap-1.5">
          <Box className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <Typography sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>{g.filename || 'Файл без имени'}</Typography>
            {b ? <StatusMark tone={BATCH_STATUS[b.status].tone}>{BATCH_STATUS[b.status].text}</StatusMark> : <StatusMark tone="off">результат истёк</StatusMark>}
          </Box>
          {b ? (
            <>
              <Typography variant="body2" color="text.secondary">
                {b.created_at ? `${time.format(new Date(b.created_at))}, ` : ''}обработано {b.processed} из {b.total}{' '}
                {plural(b.total, 'строки', 'строк', 'строк')}
              </Typography>
              {countsText(b) && <Typography variant="body2">{countsText(b)}</Typography>}
            </>
          ) : (
            <Typography variant="body2" color="text.secondary">
              Пакет хранится неделю; результата этого файла уже нет, заявки из него остались.
            </Typography>
          )}
          {b && (
            <Box className="mt-1 flex flex-wrap gap-2">
              <Button variant="outlined" size="small" href={href} onClick={(e) => navigate(e, href)}>
                Открыть строки
              </Button>
              {(b.status === 'done' || b.status === 'cancelled') && (
                <Button size="small" href={api.batchResultUrl(g.jobId)} download startIcon={<IconDownload size={18} stroke={1.5} aria-hidden />}>
                  Скачать результат
                </Button>
              )}
            </Box>
          )}
        </Box>
      </Box>
      {g.subs.length > 0 && (
        <Box sx={{ borderTop: 1, borderColor: 'divider' }}>
          <Typography className="px-5 pt-3 md:px-6" sx={{ fontSize: '0.875rem', fontWeight: 600, color: TOKENS.ink2 }}>
            Отправлено на модерацию из этого файла: {g.subs.length}
          </Typography>
          <Box component="ul" className="m-0 list-none p-0" aria-label={`Заявки из файла ${g.filename}`}>
            {g.subs.map((s, i) => (
              <SubmissionItem key={s.id} s={s} first={i === 0} />
            ))}
          </Box>
        </Box>
      )}
    </Paper>
  )
}

/**
 * Мои заявки: единичные карточки отдельно, массовая загрузка отдельно — файлы с их результатом и заявки,
 * отправленные из строк файла.
 */
export function MinePage() {
  const [items, setItems] = useState<Submission[] | null>(null)
  const [batches, setBatches] = useState<BatchState[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'single' | 'batch'>('single')

  useEffect(() => {
    api
      .submissions()
      .then(setItems)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.detail : 'не удалось загрузить заявки'))
    api
      .batches()
      .then(setBatches)
      .catch(() => setBatches([]))
  }, [])

  const single = items?.filter((s) => !s.batch) ?? null
  const groups = items && batches ? groupsOf(batches, items) : null
  const loading = tab === 'single' ? single === null : groups === null

  return (
    <Page title="Мои заявки" description="Карточки, отправленные на модерацию, и файлы массовой загрузки. Возвращённую карточку можно исправить и отправить снова.">
      {error && (
        <Alert severity="error" className="mb-4">
          {error}
        </Alert>
      )}
      <Tabs
        value={tab}
        onChange={(_, v: 'single' | 'batch') => setTab(v)}
        aria-label="Какие заявки показать"
        className="mb-4"
        sx={{ bgcolor: 'background.paper', px: { xs: 1, md: 2 }, '& .MuiTab-root': { minHeight: 52, fontWeight: 600 } }}
      >
        <Tab value="single" label={`Единичные${single ? ` (${single.length})` : ''}`} />
        <Tab value="batch" label={`Массовая загрузка${groups ? ` (${groups.length})` : ''}`} />
      </Tabs>
      {loading && !error && <Skeleton variant="rounded" height={240} />}
      {tab === 'single' && single && single.length === 0 && (
        <Empty title="Единичных заявок пока нет" text="Заполните карточку в «Создании СТЕ» и отправьте её на модерацию." action={{ label: 'Создать СТЕ', href: '/' }} />
      )}
      {tab === 'single' && single && single.length > 0 && (
        <Paper variant="outlined" className="overflow-hidden">
          <Box component="ul" className="m-0 list-none p-0" aria-label="Единичные заявки">
            {single.map((s, i) => (
              <SubmissionItem key={s.id} s={s} first={i === 0} />
            ))}
          </Box>
        </Paper>
      )}
      {tab === 'batch' && groups && groups.length === 0 && (
        <Empty title="Файлов пока нет" text="Загрузите таблицу с товарами в «Массовой загрузке»: модуль заполнит каждую строку." action={{ label: 'Массовая загрузка', href: '/batch' }} />
      )}
      {tab === 'batch' && groups && groups.length > 0 && (
        <Box className="flex flex-col gap-3">
          {groups.map((g) => (
            <BatchGroup key={g.jobId} g={g} />
          ))}
        </Box>
      )}
    </Page>
  )
}
