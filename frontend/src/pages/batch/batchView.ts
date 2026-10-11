import type { BatchRowResult, BatchState } from '../../api/v1.ts'

type Status = BatchRowResult['status']

/** Статус строки словом: так же он записан в файле с результатом (app/batch.py, STATUS_TEXT). */
export const STATUS_TEXT: Record<Status, string> = {
  ready: 'готово',
  needs_answer: 'нужен ответ',
  needs_fix: 'нужно исправить',
  not_found: 'нет в справочнике',
  skipped: 'пустая строка',
  failed: 'сбой',
}

/** Строки по статусам: счётчики сервера (есть и без строк — у большого пакета), иначе по строкам. */
export function summarize(state: BatchState): Record<Status, number> {
  const counts: Record<Status, number> = { ready: 0, needs_answer: 0, needs_fix: 0, not_found: 0, skipped: 0, failed: 0 }
  const server = Object.entries(state.counts ?? {})
  if (server.length > 0) {
    for (const [status, n] of server) if (status in counts) counts[status as Status] += n
    return counts
  }
  for (const r of state.rows) counts[r.status] += 1
  return counts
}

export function isFinished(state: BatchState): boolean {
  return state.status === 'done' || state.status === 'failed' || state.status === 'cancelled'
}

export function canCancel(state: BatchState): boolean {
  return state.status === 'queued' || state.status === 'running'
}

/** Ответ старого опроса не отменяет уже подтверждённую отмену и не уменьшает прогресс. */
export function reconcileBatchState(current: BatchState | null, next: BatchState): BatchState {
  if (!current || current.job_id !== next.job_id) return next
  if (current.status === 'cancelled' && next.status !== 'cancelled') return current
  if (next.processed < current.processed) return next.status === 'cancelled' && current.status !== 'cancelled' ? { ...current, status: 'cancelled' } : current
  const rows = new Map(current.rows.map((r) => [r.row, r]))
  for (const row of next.rows) rows.set(row.row, row)
  return { ...next, rows: [...rows.values()].sort((a, b) => a.row - b.row) }
}
