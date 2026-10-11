import { IconUpload } from '@tabler/icons-react'
import {
  Alert,
  Box,
  Button,
  LinearProgress,
  Paper,
  Typography,
} from '@mui/material'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError, api, type BatchRowResult, type BatchState } from '../../api/v1.ts'
import { pendingFile } from '../../lib/pendingFile.ts'
import { go } from '../../lib/router'
import { Page } from '../../shell/Page'
import { PREFILL_KEY, type Prefill } from '../create/createModel.ts'
import { canCancel, isFinished, reconcileBatchState, summarize } from './batchView.ts'
import { BatchResults } from './BatchResults.tsx'
import { loadLastBatch, saveLastBatch } from './lastBatch.ts'

const POLL_MS = 1000

/**
 * Строка пакета уходит в форму «Создание СТЕ» целиком: наименование, описание и характеристики файла.
 * Пакет устарел (404) — переносится хотя бы наименование.
 */
async function openInForm(jobId: string, row: BatchRowResult, filename: string) {
  const input = await api.batchRow(jobId, row.row).catch(() => null)
  const prefill: Prefill = {
    name: input?.name ?? row.name,
    description: input?.description ?? '',
    characteristics: input?.characteristics ?? [],
    revise: null,
    photoId: null,
    batch: { job_id: jobId, row: row.row, filename },
  }
  try {
    sessionStorage.setItem(PREFILL_KEY, JSON.stringify(prefill))
  } catch {
    // без sessionStorage форма просто откроется пустой
  }
  go('/')
}

export function BatchPage() {
  // Файл из мастера («В файле N товаров» → «Открыть массовую загрузку») — сразу выбран и уходит в работу.
  const [file, setFile] = useState<File | null>(() => pendingFile.peek())
  const fromWizard = useRef(file)
  // Пакет по ссылке из «Моих заявок» (`/batch?job=…`) важнее последнего пакета вкладки.
  const [linked] = useState(() => (file ? null : new URLSearchParams(window.location.search).get('job')))
  // Вернулся на страницу без нового файла — тот же пакет, что был (`lastBatch.ts`).
  const [last, setLast] = useState(() => (file || linked ? null : loadLastBatch()))
  const [jobId, setJobId] = useState<string | null>(linked ?? last?.jobId ?? null)
  const [state, setState] = useState<BatchState | null>(null)
  const [uploading, setUploading] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!jobId) return
    let stopped = false
    let timer: number | undefined
    const tick = async () => {
      try {
        const next = await api.batch(jobId, 0)
        if (stopped) return
        setState((current) => reconcileBatchState(current, next))
        if (!isFinished(next)) timer = window.setTimeout(tick, POLL_MS)
      } catch (e) {
        if (stopped) return
        if (e instanceof ApiError && e.status === 404) {
          // Пакет истёк на сервере: прежний результат не вернуть, файл нужно загрузить снова.
          saveLastBatch(null)
          setLast(null)
          setJobId(null)
          setError('Прежний пакет больше недоступен: загрузите файл снова.')
        } else setError(e instanceof ApiError ? e.detail : 'не удалось получить состояние пакета')
      }
    }
    void tick()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [jobId])

  const upload = async (chosen: File | null = file) => {
    if (!chosen) return
    setUploading(true)
    setError(null)
    setState(null)
    setJobId(null)
    try {
      const accepted = await api.uploadBatch(chosen)
      saveLastBatch({ jobId: accepted.job_id, filename: chosen.name })
      setLast(null)
      setJobId(accepted.job_id)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'не удалось загрузить файл')
    } finally {
      setUploading(false)
    }
  }

  const refreshResult = async () => {
    if (!jobId || !state) return
    setRefreshing(true)
    setError(null)
    try {
      const next = await api.batch(jobId, 0)
      setState((current) => current?.job_id === jobId ? reconcileBatchState(current, next) : current)
      setRefreshVersion((n) => n + 1)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'не удалось обновить результаты')
    } finally {
      setRefreshing(false)
    }
  }

  const cancel = async () => {
    if (!jobId || !state || !canCancel(state)) return
    setCancelling(true)
    setError(null)
    try {
      const next = await api.cancelBatch(jobId)
      setState((current) => current?.job_id === jobId ? reconcileBatchState(current, next) : current)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'не удалось прервать пакет, попробуйте снова')
    } finally {
      setCancelling(false)
    }
  }

  const uploadRef = useRef(upload)
  useLayoutEffect(() => {
    uploadRef.current = upload
  })
  useEffect(() => {
    const handed = fromWizard.current
    if (!handed) return
    fromWizard.current = null
    pendingFile.take()
    void uploadRef.current(handed)
  }, [])

  const counts = state ? summarize(state) : null
  const done = state ? isFinished(state) : false
  const progress = state && state.total ? Math.round((state.processed / state.total) * 100) : 0

  return (
    <Page
      title="Массовая загрузка"
      description="Файл с товарами (Excel xlsx и xls, ods, csv, json, Word с таблицей товаров или архив zip и 7z): модуль подбирает код КТРУ и заполняет характеристики по каждой строке. Результат скачивается тем же файлом со статусом и замечаниями."
    >
      <Paper variant="outlined" className="p-5 md:p-6">
        <Box className="flex flex-wrap items-center gap-3">
          <input
            ref={input}
            type="file"
            accept=".xlsx,.xlsm,.xls,.xlsb,.ods,.csv,.tsv,.txt,.json,.jsonl,.ndjson,.docx,.zip,.7z"
            className="sr-only"
            aria-label="Файл с товарами"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <Button variant="outlined" startIcon={<IconUpload size={20} stroke={1.5} aria-hidden />} onClick={() => input.current?.click()}>
            Выбрать файл
          </Button>
          <Typography color={file ? 'text.primary' : 'text.secondary'} className="min-w-0 truncate">
            {file ? file.name : (last?.filename ?? 'Файл не выбран')}
          </Typography>
          <Button variant="contained" className="ml-auto" disabled={!file || uploading || cancelling} onClick={() => void upload()}>
            {uploading ? 'Загружаем…' : 'Загрузить'}
          </Button>
        </Box>
        {error && (
          <Alert severity="error" className="mt-4">
            {error}
          </Alert>
        )}
      </Paper>

      {state && counts && (
        <Paper variant="outlined" className="mt-6 overflow-hidden">
          {!done && <LinearProgress variant="determinate" value={progress} />}
          <Box className="flex flex-wrap items-center gap-3 px-5 py-4 md:px-6">
            <Box className="min-w-0">
              <Typography variant="h6" component="h2">
                {state.filename}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {state.status === 'failed' ? 'Обработка прервалась.' : `Обработано ${state.processed} из ${state.total}.`}
              </Typography>
            </Box>
            {canCancel(state) && jobId && (
              <Button variant="outlined" color="error" className="ml-auto" disabled={cancelling || uploading} onClick={() => void cancel()}>
                {cancelling ? 'Прерываем…' : 'Прервать'}
              </Button>
            )}
            {state.status === 'cancelled' && (
              <Button variant="outlined" disabled={refreshing || cancelling} onClick={() => void refreshResult()}>
                {refreshing ? 'Обновляем…' : 'Обновить результаты'}
              </Button>
            )}
            {done && jobId && state.status !== 'failed' && (
              <Button variant="contained" className="ml-auto" href={api.batchResultUrl(jobId)} download>
                {state.status === 'cancelled' ? 'Скачать частичный результат' : 'Скачать файл с результатом'}
              </Button>
            )}
          </Box>
          {state.status === 'cancelled' && (
            <Alert severity="info" className="mx-5 mb-4 md:mx-6">
              Отмена запрошена. Новые строки не запускаются, уже начатые могут завершиться.
              Обновите результаты, чтобы увидеть их. В файле будут результаты, доступные на момент скачивания.
            </Alert>
          )}
          {jobId && <BatchResults key={jobId} jobId={jobId} processed={state.processed} counts={counts} refreshVersion={refreshVersion} onOpen={(row) => void openInForm(jobId, row, state.filename)} onRefresh={() => void refreshResult()} />}
        </Paper>
      )}
    </Page>
  )
}
