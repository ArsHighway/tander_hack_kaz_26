export type RouteId = 'create' | 'batch' | 'mine' | 'moderation' | 'metrics' | 'developers' | 'not-found'
export type Role = 'supplier' | 'moderator'
export type NavItem = { href: string; label: string }

const PATHS: Record<string, RouteId> = {
  '/': 'create',
  '/batch': 'batch',
  '/my': 'mine',
  '/moderation': 'moderation',
  '/metrics': 'metrics',
  '/developers': 'developers',
}

export function resolveRoute(path: string): RouteId {
  const clean = path.length > 1 ? path.replace(/\/+$/, '') : path
  return Object.hasOwn(PATHS, clean) ? PATHS[clean] : 'not-found'
}

/** Роли портала и их разделы: поставщик заводит карточки, модератор портала их проверяет. */
export const ROLES: { id: Role; label: string; items: NavItem[] }[] = [
  {
    id: 'supplier',
    label: 'Поставщик',
    items: [
      { href: '/', label: 'Создание СТЕ' },
      { href: '/batch', label: 'Массовая загрузка' },
      { href: '/my', label: 'Мои заявки' },
    ],
  },
  { id: 'moderator', label: 'Модератор', items: [{ href: '/moderation', label: 'Заявки' }] },
]

/** Общие экраны вне разделов роли: метрики и описание API (открываются по адресу). */
export const NAV_ASIDE: NavItem[] = [
  { href: '/metrics', label: 'Метрики' },
  { href: '/developers', label: 'API' },
]

/** Роль экрана; «Метрики», «API» и 404 общие, на них остаётся прежняя роль. */
export function roleOf(route: RouteId, last: Role): Role {
  if (route === 'create' || route === 'batch' || route === 'mine') return 'supplier'
  if (route === 'moderation') return 'moderator'
  return last
}

/** Пункт навигации, который подсвечивается на экране (у 404 — никакой). */
export function navHref(route: RouteId): string | null {
  return Object.entries(PATHS).find(([, id]) => id === route)?.[0] ?? null
}
