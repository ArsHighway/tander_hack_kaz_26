import { Box, Typography } from '@mui/material'
import { IconChevronRight } from '@tabler/icons-react'
import { TOKENS } from '../../../theme'
import type { Trail } from './trail.ts'

/**
 * Как модуль сузил выбор — одной строкой со стрелками: «Типы КТРУ и категории портала: 652 › Подходят по
 * описанию: 16 › Размер: 52: 15». Объяснение для того, кто хочет проверить, а не стена текста над вариантами.
 */
export function KtruTrail({ trail }: { trail: Trail }) {
  if (!trail.length) return null
  return (
    <Box component="section" aria-label="Как выбрана позиция КТРУ">
      <Typography component="h3" sx={{ fontSize: '0.8125rem', fontWeight: 600, color: TOKENS.mute, mb: 0.5 }}>
        Как сузили выбор
      </Typography>
      <Box component="ol" className="m-0 flex list-none flex-wrap items-center gap-x-1 gap-y-0.5 p-0" sx={{ fontSize: '0.875rem', color: TOKENS.ink2, overflowWrap: 'anywhere' }}>
        {trail.map((s, i) => (
          <li key={`${i}-${s.label}`} className="inline-flex items-center gap-1">
            {i > 0 && <IconChevronRight size={14} stroke={1.5} color={TOKENS.field} aria-hidden />}
            <span>
              {s.label}:{' '}
              <Box component="span" sx={{ fontWeight: 700, color: TOKENS.ink }}>
                {s.remaining}
              </Box>
            </span>
          </li>
        ))}
      </Box>
    </Box>
  )
}
