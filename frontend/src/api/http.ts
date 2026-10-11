// Запросы к API модуля. Ошибка — всегда ApiError с текстом для человека: экраны показывают detail как есть.

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
  }
}

type ValidationItem = { loc?: (string | number)[]; msg?: string }

export function toApiError(status: number, body: string): ApiError {
  if (status === 0) return new ApiError(0, 'сервис недоступен')
  try {
    const parsed = JSON.parse(body) as { detail?: string | ValidationItem[] }
    if (typeof parsed.detail === 'string') return new ApiError(status, parsed.detail)
    if (Array.isArray(parsed.detail)) {
      const text = parsed.detail
        .map((d) => [d.loc?.filter((p) => p !== 'body').join('.'), d.msg].filter(Boolean).join(': '))
        .join('; ')
      return new ApiError(status, text || `сервис ответил ошибкой ${status}`)
    }
  } catch {
    // не JSON — ниже общий текст
  }
  return new ApiError(status, `сервис ответил ошибкой ${status}`)
}

async function send(path: string, init: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    throw toApiError(0, '')
  }
  if (!res.ok) throw toApiError(res.status, await res.text())
  return res
}

export async function getJson<T>(path: string): Promise<T> {
  return (await send(path, { headers: { Accept: 'application/json' } })).json() as Promise<T>
}

export async function deleteJson<T>(path: string): Promise<T> {
  return (await send(path, { method: 'DELETE', headers: { Accept: 'application/json' } })).json() as Promise<T>
}

export async function postJson<T>(path: string, body: unknown, method: 'POST' | 'PUT' = 'POST'): Promise<T> {
  const res = await send(path, {
    method,
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  return res.json() as Promise<T>
}

export async function postForm<T>(path: string, form: FormData): Promise<T> {
  return (await send(path, { method: 'POST', body: form })).json() as Promise<T>
}
