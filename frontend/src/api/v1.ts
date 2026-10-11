// Клиент модуля автозаполнения. Типы — из OpenAPI бэкенда (schema.d.ts, `npm run gen:api`), руками не пишутся.
import { ApiError, deleteJson, getJson, postForm, postJson } from './http.ts'
import type { components } from './schema'

type S = components['schemas']
export type AutofillOut = S['AutofillOut']
export type CharacteristicOut = S['CharacteristicOut']
export type KtruState = S['KtruState']
export type Question = S['Question']
export type Option = S['Option']
export type Candidate = S['Candidate']
export type SimilarSku = S['SimilarSku']
export type TwinOut = S['TwinOut']
export type ExtractOut = S['ExtractOut']
export type Chain = S['Chain']
export type Span = S['Span']
export type Issue = S['Issue']
export type CardIn = S['CardIn']
export type CharIn = S['CharIn']
export type CheckOut = S['CheckOut']
export type Position = S['Position']
export type BatchState = S['BatchState']
export type BatchResultPage = S['BatchResultPage']
export type BatchRowResult = S['BatchRowResult']
export type BatchRowIn = S['BatchRowIn']
export type BatchAccepted = S['BatchAccepted']
export type MetricsOut = S['MetricsOut']
export type MetricRow = S['MetricRow']
export type PortalReport = S['PortalReport']
export type HoldoutOut = S['HoldoutOut']
export type AuditRule = S['AuditRule']
export type AuditExample = S['AuditExample']
export type Submission = S['Submission']
export type SubmissionView = S['SubmissionView']
export type QueuedSubmission = S['QueuedSubmission']
export type Triage = S['Triage']
export type AcceptedClean = S['AcceptedClean']
export type SubmissionIn = S['SubmissionIn']
export type SubmissionBatch = S['SubmissionBatch']
export type DecisionIn = S['DecisionIn']
export type ModuleSettings = S['ModuleSettings']

export { ApiError }

const V1 = '/api/v1'
const DEMO = '/api/demo'

/** Фото похожей СТЕ с портала через свой прокси с кэшем (404 — фото нет или портал не ответил). */
export const skuImageUrl = (id: number): string => `${V1}/images/sku/${id}`
/** Картинка категории портала (по `portal_id` позиции справочника). */
export const categoryImageUrl = (portalId: number): string => `${V1}/images/category/${portalId}`
/** Фото товара, загруженное поставщиком в мастере (`photo_id` заявки). */
export const photoUrl = (photoId: number): string => `${DEMO}/photos/${photoId}`

export const api = {
  autofill: (name: string, description: string) => postJson<AutofillOut>(`${V1}/autofill`, { name, description }),
  answer: (sessionId: string, characteristic: string, value: string, stage: 'category' | 'ktru' = 'category') =>
    postJson<AutofillOut>(`${V1}/autofill/${encodeURIComponent(sessionId)}/answer`, { characteristic, value, stage }),
  check: (card: CardIn) => postJson<CheckOut>(`${V1}/check`, card),
  ktru: (code: string) => getJson<Position>(`${V1}/ktru/${encodeURIComponent(code)}`),
  uploadBatch: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return postForm<BatchAccepted>(`${V1}/batch`, form)
  },
  /** limit — сколько первых строк: 0 — только счётчики (опрос большого пакета), нет — все. */
  /** Последние пакеты, новые сверху, только счётчики: «Мои заявки → Пакеты». */
  batches: () => getJson<BatchState[]>(`${V1}/batch`),
  batch: (jobId: string, limit?: number) =>
    getJson<BatchState>(`${V1}/batch/${encodeURIComponent(jobId)}${limit === undefined ? '' : `?limit=${limit}`}`),
  cancelBatch: (jobId: string) => deleteJson<BatchState>(`${V1}/batch/${encodeURIComponent(jobId)}`),
  /** Фильтр по всему пакету; курсор — номер строки последней показанной страницы. */
  batchResults: (jobId: string, status?: BatchRowResult['status'], afterRow = 0) => {
    const query = new URLSearchParams({ limit: '50', after_row: String(afterRow) })
    if (status) query.set('status', status)
    return getJson<BatchResultPage>(`${V1}/batch/${encodeURIComponent(jobId)}/results?${query}`)
  },
  /** Строка файла как её прислал поставщик: открыть в форме карточки. */
  batchRow: (jobId: string, row: number) => getJson<BatchRowIn>(`${V1}/batch/${encodeURIComponent(jobId)}/rows/${row}`),
  batchResultUrl: (jobId: string) => `${V1}/batch/${encodeURIComponent(jobId)}/result.xlsx`,
  /** null — оценка ещё не запускалась (404). */
  metrics: async (): Promise<MetricsOut | null> => {
    try {
      return await getJson<MetricsOut>(`${V1}/metrics`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null
      throw e
    }
  },
  /** null — отложенная выборка ещё не оценивалась (404). */
  holdout: async (): Promise<HoldoutOut | null> => {
    try {
      return await getJson<HoldoutOut>(`${V1}/metrics/holdout`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null
      throw e
    }
  },
  /** null — отчёт по карточкам портала ещё не собран (404). */
  portalReport: async (): Promise<PortalReport | null> => {
    try {
      return await getJson<PortalReport>(`${V1}/metrics/portal`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null
      throw e
    }
  },
  submit: (s: SubmissionIn) => postJson<Submission>(`${DEMO}/submissions`, s),
  extract: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return postForm<ExtractOut>(`${V1}/extract`, form)
  },
  uploadPhoto: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return postForm<{ photo_id: number }>(`${DEMO}/photos`, form)
  },
  submissions: () => getJson<QueuedSubmission[]>(`${DEMO}/submissions`),
  /** Одобрить в один клик заявки из подтверждённого списка, которые всё ещё без замечаний модуля (`triage.clean`). */
  acceptClean: (ids: number[]) => postJson<AcceptedClean>(`${DEMO}/submissions/accept-clean`, { ids }),
  submission: (id: number) => getJson<SubmissionView>(`${DEMO}/submissions/${id}`),
  /** Исправленная возвращённая заявка снова уходит модератору. */
  resubmit: (id: number, s: SubmissionIn) => postJson<Submission>(`${DEMO}/submissions/${id}`, s, 'PUT'),
  decide: (id: number, d: DecisionIn) => postJson<Submission>(`${DEMO}/submissions/${id}/decision`, d),
  /** Настройки модуля: включена ли модель (общая для API и воркеров). */
  settings: () => getJson<ModuleSettings>(`${V1}/settings`),
  /** Включить или выключить модель; только у модератора. */
  setLlmEnabled: (enabled: boolean) => postJson<ModuleSettings>(`${V1}/settings`, { llm_enabled: enabled }, 'PUT'),
}
