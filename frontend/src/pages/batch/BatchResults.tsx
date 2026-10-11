import { Alert, Box, Button, Chip, LinearProgress, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material'
import { useEffect, useState } from 'react'
import { ApiError, api, type BatchResultPage, type BatchRowResult } from '../../api/v1.ts'
import { StatusMark, type StatusTone } from '../../shell/StatusMark'
import { MONO } from '../../theme'
import { STATUS_TEXT } from './batchView.ts'

type Status = BatchRowResult['status']

const STATUS_COLOR: Record<Status, 'success' | 'error' | 'warning' | 'default'> = {
  ready: 'success', needs_answer: 'warning', needs_fix: 'error', not_found: 'default', skipped: 'default', failed: 'error',
}

// В строке таблицы статус портала: кружок и капс.
const STATUS_TONE: Record<Status, StatusTone> = {
  ready: 'ok', needs_answer: 'wait', needs_fix: 'back', not_found: 'off', skipped: 'off', failed: 'back',
}

function remark(r: BatchRowResult): string {
  if (r.error) return r.error
  if (r.note) return r.note
  return r.issues.length ? `${r.issues.length}: ${r.issues[0].message}` : 'нет'
}

export function BatchResults({ jobId, processed, counts, refreshVersion, onOpen, onRefresh }: {
  jobId: string
  processed: number
  counts: Record<Status, number>
  refreshVersion: number
  onOpen: (row: BatchRowResult) => void
  onRefresh: () => void
}) {
  const [filter, setFilter] = useState<Status | undefined>()
  const [cursors, setCursors] = useState<number[]>([])
  const [revision, setRevision] = useState(0)
  const [page, setPage] = useState<{ selection: string; key: string; result: BatchResultPage } | null>(null)
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null)
  const afterRow = cursors.at(-1) ?? 0
  const selection = `${filter ?? 'all'}:${afterRow}`
  const requestKey = `${selection}:${revision}:${processed}:${refreshVersion}`
  const loading = page?.key !== requestKey && failure?.key !== requestKey
  const error = failure?.key === requestKey ? failure.message : null
  const visible = page?.selection === selection ? page.result : null
  const matched = filter ? counts[filter] : processed

  useEffect(() => {
    let stopped = false
    void api.batchResults(jobId, filter, afterRow).then((result) => {
      // Ответ предыдущего фильтра/страницы не заменяет текущий выбор.
      if (stopped) return
      setPage({ selection, key: requestKey, result })
      setFailure(null)
    }).catch((e: unknown) => {
      if (stopped) return
      setFailure({ key: requestKey, message: e instanceof ApiError ? e.detail : 'не удалось получить строки пакета' })
    })
    return () => { stopped = true }
  }, [jobId, filter, afterRow, selection, requestKey])

  const select = (status?: Status) => {
    setFilter(status)
    setCursors([])
  }
  const refresh = () => {
    setCursors([])
    setRevision((n) => n + 1)
    onRefresh()
  }

  return (
    <Box component="section" aria-label="Результаты пакета">
      <Box className="flex flex-wrap gap-2 px-5 pb-4 md:px-6" role="group" aria-label="Фильтр по статусу">
        <Chip label={`Все: ${processed}`} variant={filter === undefined ? 'filled' : 'outlined'} aria-pressed={filter === undefined} onClick={() => select()} />
        {(Object.keys(STATUS_TEXT) as Status[]).map((s) => (
          <Chip key={s} label={`${STATUS_TEXT[s]}: ${counts[s]}`} color={STATUS_COLOR[s]} variant={filter === s ? 'filled' : 'outlined'} aria-pressed={filter === s} onClick={() => select(s)} />
        ))}
      </Box>
      <Box className="flex flex-wrap items-center gap-3 px-5 pb-4 md:px-6">
        <Typography variant="body2" color="text.secondary" aria-live="polite">
          {visible ? `Показано ${visible.rows.length} из ${Math.max(matched, visible.matched)}. Страница ${cursors.length + 1}.` : 'Загружаем строки…'}
        </Typography>
        <Button size="small" disabled={loading} onClick={refresh}>Обновить строки</Button>
      </Box>
      {loading && <LinearProgress aria-label="Загрузка строк" />}
      {error && <Alert severity="error" className="mx-5 mb-4 md:mx-6" action={<Button color="inherit" onClick={refresh}>Повторить</Button>}>{error}</Alert>}
      {visible && !loading && !error && visible.rows.length === 0 && (
        <Typography className="px-5 pb-4 md:px-6" color="text.secondary">
          {filter ? 'Обработанных строк с этим статусом пока нет.' : 'Обработанных строк пока нет.'}
        </Typography>
      )}
      <TableContainer>
        <Table size="small" aria-label="Строки файла">
          <TableHead>
            <TableRow>
              <TableCell>№</TableCell><TableCell>Наименование</TableCell><TableCell>Категория (КПГЗ)</TableCell><TableCell>Код КТРУ</TableCell>
              <TableCell align="right">Заполнено</TableCell><TableCell>Замечания</TableCell><TableCell>Статус</TableCell>
              <TableCell><span className="sr-only">Действие</span></TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(visible?.rows ?? []).map((r) => (
              <TableRow key={r.row}>
                <TableCell sx={{ color: 'text.secondary' }}>{r.row}</TableCell>
                <TableCell>{r.name || 'без наименования'}</TableCell>
                <TableCell sx={{ fontFamily: MONO, fontSize: '0.875rem', whiteSpace: 'nowrap' }}>{r.category_code ?? 'не определена'}</TableCell>
                <TableCell sx={{ fontFamily: MONO, fontSize: '0.875rem', whiteSpace: 'nowrap' }}>{r.ktru_code ?? 'не определён'}</TableCell>
                <TableCell align="right" title={r.hinted ? `из них ${r.hinted}: подсказки СТЕ портала, проверьте` : undefined}>
                  {r.filled}
                  {r.hinted > 0 && <Box component="span" sx={{ color: 'text.secondary' }}>{`, подсказок ${r.hinted}`}</Box>}
                </TableCell>
                <TableCell sx={{ color: r.error ? 'error.main' : 'text.secondary', maxWidth: 360 }}>{remark(r)}</TableCell>
                <TableCell><StatusMark tone={STATUS_TONE[r.status]}>{STATUS_TEXT[r.status]}</StatusMark></TableCell>
                <TableCell>{r.name && r.status !== 'ready' && <Button size="small" onClick={() => onOpen(r)} sx={{ whiteSpace: 'nowrap' }}>Открыть в форме</Button>}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <Box className="flex flex-wrap justify-end gap-2 px-5 py-4 md:px-6">
        <Button variant="outlined" disabled={loading || cursors.length === 0} onClick={() => setCursors((current) => current.slice(0, -1))}>Назад</Button>
        <Button variant="outlined" disabled={loading || !visible || visible.next_after_row == null} onClick={() => {
          const next = visible?.next_after_row
          if (next != null) setCursors((current) => [...current, next])
        }}>Далее</Button>
      </Box>
    </Box>
  )
}
