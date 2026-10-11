import { Box } from '@mui/material'
import type { ReactNode } from 'react'
import { MONO, TOKENS } from '../../../theme'

/** Подсказка клавиши рядом с действием. Для экранных дикторов дублирует `aria-keyshortcuts` кнопки — скрыта. */
export function Kbd({ children, inverse = false }: { children: ReactNode; inverse?: boolean }) {
  return (
    <Box
      component="kbd"
      aria-hidden
      sx={{
        fontFamily: MONO,
        fontSize: '0.75rem',
        lineHeight: '1rem',
        px: '5px',
        borderRadius: '4px',
        border: '1px solid',
        borderBottomWidth: '2px',
        borderColor: inverse ? 'rgba(255,255,255,0.5)' : TOKENS.field,
        color: inverse ? '#ffffff' : TOKENS.ink2,
        bgcolor: inverse ? 'transparent' : '#ffffff',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </Box>
  )
}
