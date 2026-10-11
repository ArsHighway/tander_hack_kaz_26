import { AppBar, Box, ButtonBase, Tab, Tabs, Toolbar, Typography } from '@mui/material'
import { IconPackage, IconShieldCheck } from '@tabler/icons-react'
import type { ReactNode } from 'react'
import { navigate } from '../lib/router'
import { ROLES, navHref, type Role, type RouteId } from '../routes'
import { TOKENS } from '../theme'
import { BrandMark } from './BrandMark'

type Props = { route: RouteId; role: Role; onRole: (role: Role) => void; children: ReactNode }

/** С 1024 px шапка в одну строку высотой 80 px; уже — две: знак и роль сверху, разделы под ними. */
const WIDE = '@media (min-width: 1024px)'

// Кружок роли: поставщик — коробка товара на голубом, модератор — щит проверки на розовом (красный портала).
const AVATAR: Record<Role, { Icon: typeof IconPackage; bg: string; fg: string }> = {
  supplier: { Icon: IconPackage, bg: TOKENS.accSoft, fg: TOKENS.acc },
  moderator: { Icon: IconShieldCheck, bg: '#fbe6e4', fg: TOKENS.cta },
}

/** Вертикальный разделитель ячеек шапки, как у портала перед блоком пользователя. */
function Rule() {
  return <Box aria-hidden sx={{ alignSelf: 'stretch', width: '1px', bgcolor: TOKENS.line, my: 1.5, flex: 'none', [WIDE]: { my: 2.5 } }} />
}

/**
 * Роль — как блок пользователя в шапке портала: кружок со значком роли, роль жирным, под ней ссылка на другую.
 * Одним нажатием: жюри переходит между ролями на показе.
 */
function RoleBlock({ role, onRole }: { role: Role; onRole: (role: Role) => void }) {
  const current = ROLES.find((r) => r.id === role) ?? ROLES[0]
  const other = ROLES.find((r) => r.id !== role) ?? ROLES[0]
  const { Icon, bg, fg } = AVATAR[role]
  return (
    <Box role="group" aria-label="Роль" className="flex items-center gap-2.5 pl-3 min-[1024px]:pl-5">
      <Box
        aria-hidden
        sx={{ width: 40, height: 40, borderRadius: '50%', bgcolor: bg, color: fg, display: 'grid', placeItems: 'center', flex: 'none' }}
      >
        <Icon size={22} stroke={1.5} />
      </Box>
      <Box className="flex min-w-0 flex-col items-start">
        <Typography component="span" sx={{ fontWeight: 700, fontSize: '0.9375rem', lineHeight: '1.125rem', color: TOKENS.ink }}>
          {current.label}
        </Typography>
        <ButtonBase
          onClick={() => onRole(other.id)}
          sx={{
            fontSize: '0.8125rem',
            lineHeight: '1rem',
            color: TOKENS.acc,
            textAlign: 'left',
            whiteSpace: 'nowrap',
            '&:hover': { textDecoration: 'underline' },
            '&.Mui-focusVisible': { outline: `2px solid ${TOKENS.acc}`, outlineOffset: 2 },
            '@media (pointer: coarse)': { minHeight: 32 },
          }}
        >
          Войти как {other.label.toLowerCase()}
        </ButtonBase>
      </Box>
    </Box>
  )
}

/** Шапка портала: знак, разделы роли капсом, блок роли справа. */
export function AppShell({ route, role, onRole, children }: Props) {
  const current = navHref(route)
  const items = ROLES.find((r) => r.id === role)?.items ?? []
  const tab = items.some((i) => i.href === current) ? current : false
  return (
    <Box className="flex min-h-dvh flex-col">
      <a
        href="#content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:bg-white focus:px-4 focus:py-2"
      >
        Перейти к содержимому
      </a>
      {/* Шапка портала: белая, 80 px, тень Main Blue вместо линии (UI-кит, 06_Header and footers). */}
      <AppBar position="sticky" color="inherit" elevation={0} sx={{ bgcolor: '#ffffff', boxShadow: '0 10px 30px rgba(38, 75, 130, 0.1)' }}>
        <Toolbar
          disableGutters
          className="mx-auto w-full max-w-[max(1440px,74vw)] flex-wrap items-stretch px-4 md:px-6 min-[1024px]:flex-nowrap"
          sx={{ minHeight: 64, [WIDE]: { minHeight: 80 } }}
        >
          <Box
            component="a"
            href="/"
            onClick={(e) => navigate(e, '/')}
            className="order-1 flex items-center self-center py-3 min-[1024px]:py-0"
            sx={{ textDecoration: 'none', '&:focus-visible': { outline: `2px solid ${TOKENS.acc}`, outlineOffset: 4 } }}
          >
            <BrandMark compact />
          </Box>
          <Box className="order-3 hidden min-[1024px]:order-2 min-[1024px]:ml-6 min-[1024px]:flex">
            <Rule />
          </Box>
          <Tabs
            value={tab}
            aria-label="Разделы"
            variant="scrollable"
            scrollButtons={false}
            textColor="inherit"
            className="order-4 w-full min-[1024px]:order-3 min-[1024px]:ml-2 min-[1024px]:w-auto"
            sx={{
              minHeight: 44,
              borderTop: `1px solid ${TOKENS.line}`,
              [WIDE]: { minHeight: 80, borderTop: 'none' },
              // Вкладки портала: капс SemiBold 14 с разрядкой 1 px, активная Main Blue с линией 2 px.
              '& .MuiTabs-scroller, & .MuiTabs-list': { height: '100%' },
              '& .MuiTabs-indicator': { bgcolor: TOKENS.acc, height: 2 },
              '& .MuiTab-root': {
                minHeight: 44,
                [WIDE]: { minHeight: 80 },
                px: 1.5,
                minWidth: 0,
                fontWeight: 600,
                fontSize: '0.9375rem',
                letterSpacing: '1px',
                textTransform: 'uppercase',
                color: TOKENS.mute,
                opacity: 1,
              },
              '& .MuiTab-root:hover': { color: TOKENS.acc },
              '& .MuiTab-root.Mui-selected': { color: TOKENS.acc },
            }}
          >
            {items.map((i) => (
              <Tab key={i.href} value={i.href} label={i.label} component="a" href={i.href} onClick={(e) => navigate(e, i.href)} />
            ))}
          </Tabs>
          <Box className="order-2 ml-auto flex items-stretch min-[1024px]:order-4">
            <Rule />
            <RoleBlock role={role} onRole={onRole} />
          </Box>
        </Toolbar>
      </AppBar>
      <Box className="flex flex-1 flex-col">{children}</Box>
    </Box>
  )
}
