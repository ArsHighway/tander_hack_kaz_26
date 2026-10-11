import { Box } from '@mui/material'
import { TOKENS } from '../theme'

export type StatusTone = 'wait' | 'ok' | 'back' | 'info' | 'off'

// Статус портала (UI-кит, 05_Status): кружок цветом статуса и капс с разрядкой. Кружок — яркий цвет кита (графике
// хватает 3:1), подпись — тёмный оттенок того же цвета: мелкий текст должен проходить 4,5:1 и на выделенной строке.
const TONES: Record<StatusTone, { dot: string; text: string }> = {
  wait: { dot: '#f67319', text: '#7a3b00' },
  ok: { dot: '#0d9b68', text: '#0b7f55' },
  back: { dot: TOKENS.err, text: '#a8211a' },
  info: { dot: TOKENS.acc, text: TOKENS.acc },
  off: { dot: TOKENS.field, text: TOKENS.mute },
}

/** «● НА МОДЕРАЦИИ»: статус заявки или строки пакета, как в реестрах портала. */
export function StatusMark({ tone, children }: { tone: StatusTone; children: string }) {
  const t = TONES[tone]
  return (
    <Box
      component="span"
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        color: t.text,
        fontSize: '0.8125rem',
        lineHeight: '1rem',
        fontWeight: 700,
        letterSpacing: '0.5px',
        textTransform: 'uppercase',
        whiteSpace: 'nowrap',
      }}
    >
      <Box component="span" aria-hidden sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: t.dot, flex: 'none' }} />
      {children}
    </Box>
  )
}
