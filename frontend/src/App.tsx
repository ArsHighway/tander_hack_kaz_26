import { GlobalStyles, ThemeProvider } from '@mui/material'
import { StyledEngineProvider } from '@mui/material/styles'
import { Box, Skeleton } from '@mui/material'
import { Suspense, lazy, useState, type ComponentType, type LazyExoticComponent } from 'react'
import { go, usePath } from './lib/router'
import { ROLES, resolveRoute, roleOf, type Role, type RouteId } from './routes'
import { AppShell } from './shell/AppShell'
import { theme } from './theme'

// Экраны грузятся отдельными чанками: в основном остаются каркас и общая часть MUI.
const page = (load: () => Promise<Record<string, ComponentType>>, name: string): LazyExoticComponent<ComponentType> =>
  lazy(() => load().then((m) => ({ default: m[name] })))

const SCREENS: Record<RouteId, LazyExoticComponent<ComponentType>> = {
  create: page(() => import('./pages/create/CreatePage'), 'CreatePage'),
  batch: page(() => import('./pages/batch/BatchPage'), 'BatchPage'),
  mine: page(() => import('./pages/mine/MinePage'), 'MinePage'),
  moderation: page(() => import('./pages/moderation/ModerationPage'), 'ModerationPage'),
  metrics: page(() => import('./pages/metrics/MetricsPage'), 'MetricsPage'),
  developers: page(() => import('./pages/developers/DevelopersPage'), 'DevelopersPage'),
  'not-found': page(() => import('./pages/NotFound'), 'NotFound'),
}

function Loading() {
  return (
    <Box className="mx-auto w-full max-w-[max(1440px,74vw)] px-4 pt-6 md:px-6" aria-busy="true">
      <Skeleton variant="rounded" height={320} />
    </Box>
  )
}

export default function App() {
  const route = resolveRoute(usePath())
  const [last, setLast] = useState<Role>('supplier')
  const role = roleOf(route, last)
  const Screen = SCREENS[route]
  // Роль запоминается, чтобы «Метрики» и «API» не сбрасывали переключатель (обновление при рендере, без эффекта).
  if (role !== last) setLast(role)

  const switchRole = (next: Role) => {
    setLast(next)
    go(ROLES.find((r) => r.id === next)?.items[0]?.href ?? '/')
  }

  return (
    <StyledEngineProvider enableCssLayer>
      {/* Порядок слоёв объявляется первым стилем документа: MUI сильнее сброса Tailwind, утилиты Tailwind сильнее MUI. */}
      <GlobalStyles styles="@layer theme, base, mui, components, utilities;" />
      <ThemeProvider theme={theme}>
        <AppShell route={route} role={role} onRole={switchRole}>
          {/* key: новый экран начинается с чистого состояния. */}
          <Suspense fallback={<Loading />}>
            <Screen key={route} />
          </Suspense>
        </AppShell>
      </ThemeProvider>
    </StyledEngineProvider>
  )
}
