import { Box } from '@mui/material'
import type { Origin } from '../pages/create/formModel.ts'
import { TOKENS } from '../theme'

const TEXT: Record<Origin, string> = { ktru: 'КТРУ', portal: 'портал', new: 'новая' }
const TITLE: Record<Origin, string> = {
  ktru: 'Характеристика позиции КТРУ',
  portal: 'Нет в шаблоне категории, так её пишут на портале',
  new: 'Новая характеристика: её нет ни в КТРУ, ни на портале',
}

/** Пометка источника характеристики рядом с её именем: «КТРУ», «портал», «новая». */
export function OriginMark({ origin }: { origin: Origin }) {
  return (
    <Box
      component="span"
      title={TITLE[origin]}
      sx={{ ml: 0.75, fontSize: '0.8em', fontWeight: 600, color: origin === 'ktru' ? TOKENS.acc : TOKENS.mute, whiteSpace: 'nowrap' }}
    >
      {TEXT[origin]}
    </Box>
  )
}
