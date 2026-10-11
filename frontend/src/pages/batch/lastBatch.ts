// Последний пакет вкладки в sessionStorage: ушёл со страницы и вернулся — прогресс и результат на месте,
// пакет на сервере тот же (живёт в Redis своё время; истёк — 404, и запись забывается).

export const LAST_BATCH_KEY = 'batch:last:v1'

export type LastBatch = { jobId: string; filename: string }

type Box = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

function box(storage?: Box): Box | null {
  if (storage) return storage
  try {
    return globalThis.sessionStorage ?? null
  } catch {
    return null
  }
}

export function loadLastBatch(storage?: Box): LastBatch | null {
  try {
    const d = JSON.parse(box(storage)?.getItem(LAST_BATCH_KEY) ?? 'null') as Partial<LastBatch> | null
    return d && typeof d.jobId === 'string' && d.jobId && typeof d.filename === 'string' ? { jobId: d.jobId, filename: d.filename } : null
  } catch {
    return null
  }
}

/** null — забыть (пакет истёк или выбран новый файл). */
export function saveLastBatch(last: LastBatch | null, storage?: Box): void {
  try {
    const b = box(storage)
    if (!b) return
    if (last) b.setItem(LAST_BATCH_KEY, JSON.stringify(last))
    else b.removeItem(LAST_BATCH_KEY)
  } catch {
    // хранилище закрыто: пакет живёт, пока открыта страница
  }
}
