import { Box, Typography } from '@mui/material'
import type { Issue } from '../../api/v1.ts'
import { CharacteristicFields } from '../../card/CardFields'
import type { FieldState } from '../create/formModel.ts'

/**
 * Свойства из описания поставщика, которых нет ни в позиции КТРУ, ни в шаблоне портала (`origin: new`).
 * Это предложения: блок их только показывает, а решение модератора — одобрить или вернуть — остаётся прежним.
 */
export function OutsideKtruBlock({ fields, issues }: { fields: FieldState[]; issues: Map<string, Issue[]> }) {
  if (fields.length === 0) return null
  return (
    <Box component="section" aria-labelledby="outside-ktru-title" className="p-4" sx={{ border: 1, borderColor: 'divider' }}>
      <Typography id="outside-ktru-title" variant="overline" color="text.secondary" component="h3">
        Вне КТРУ: подтвердите
      </Typography>
      <Typography sx={{ fontSize: '0.9375rem', color: 'text.secondary', mb: 2 }}>
        Нет ни в позиции КТРУ, ни в шаблоне категории портала. Поставщик назвал это сам: проверьте, что свойство относится к
        товару, прежде чем одобрять заявку.
      </Typography>
      <CharacteristicFields fields={fields} issues={issues} readOnly />
    </Box>
  )
}
