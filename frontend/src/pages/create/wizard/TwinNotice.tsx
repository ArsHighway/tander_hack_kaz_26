import { Box, Link, Typography } from '@mui/material'
import type { Issue, TwinOut } from '../../../api/v1.ts'
import { TOKENS } from '../../../theme'
import { shownTwins } from './options.ts'

/**
 * «Такая СТЕ уже есть на портале»: СТЕ той же категории с тем же наименованием. Заявку-дубль модератор
 * отклоняет («Предложена другая СТЕ»), поэтому ссылки — до отправки, а не после отказа. Блок живёт, пока
 * проверка видит дубль (`duplicate` — замечание с последнего ответа модуля): после правки наименования он
 * пропадает вместе с замечанием. Двойники по модели («та же модель») здесь не показываются: у болтов одного
 * размера и картриджей к одному принтеру модель общая, а товары разные.
 * `hinted` — у части полей есть непринятые подсказки по этим СТЕ (`method: twin`).
 */
export function TwinNotice({ twins, hinted, issues }: { twins: TwinOut[]; hinted: boolean; issues: Issue[] }) {
  const same = shownTwins(twins, issues)
  if (!same.length) return null
  const many = same.length > 1
  return (
    <Box component="section" aria-labelledby="twins-title" sx={{ border: `1px solid ${TOKENS.line}`, borderRadius: 0, px: 2, py: 1.5 }}>
      <Typography id="twins-title" component="h2" sx={{ fontSize: '1rem', fontWeight: 600 }}>
        {many ? 'Такие СТЕ уже есть на портале' : 'Такая СТЕ уже есть на портале'}
      </Typography>
      <Typography sx={{ fontSize: '0.9375rem', color: TOKENS.ink2, mt: 0.5 }}>
        {`Если товар тот же, воспользуйтесь ${many ? 'одной из них' : 'ею'}: заявку-дубль модератор отклонит. Если отличается, укажите отличие в наименовании.`}
        {hinted && ` Для полей, которых нет в вашем тексте, есть подсказки по ${many ? 'ним' : 'ней'}: примите подходящие.`}
      </Typography>
      <Box component="ul" sx={{ m: 0, mt: 1, pl: 2.5, display: 'flex', flexDirection: 'column', gap: 0.5 }}>
        {same.map((t) => (
          <li key={t.id}>
            <Link href={t.url} target="_blank" rel="noopener noreferrer" sx={{ fontSize: '0.9375rem', overflowWrap: 'anywhere' }}>
              №{t.id} {t.name}
            </Link>
          </li>
        ))}
      </Box>
    </Box>
  )
}
