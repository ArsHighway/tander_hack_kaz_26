import { Box } from '@mui/material'
import { TOKENS } from '../theme'
import { PRODUCT_NAME } from './brand'

// Имя делится как у логотипа портала («ПОРТАЛ» красным, «ПОСТАВЩИКОВ» серым): «Energy» и «Team».
const [FIRST, SECOND] = PRODUCT_NAME.split(/(?=[A-ZА-Я][a-zа-я]*$)/)

/**
 * Знак модуля в манере логотипа Портала поставщиков: красный и серый квадраты, имя в две строки капсом — первое
 * слово красным жирным, второе серым светлым. Свой знак, а не логотип портала: модуль говорит своим именем.
 */
export function BrandMark({ compact }: { compact?: boolean }) {
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: '10px' }}>
      <Box component="svg" viewBox="0 0 32 32" aria-hidden sx={{ width: 32, height: 32, flex: 'none' }}>
        <rect x="0" y="7" width="25" height="25" fill="#9aa1ab" />
        <rect x="7" y="0" width="25" height="25" fill={TOKENS.cta} />
        <rect x="14" y="7" width="11" height="11" fill="#ffffff" />
      </Box>
      {/* На телефоне в шапке только знак; имя остаётся для чтения с экрана — у ссылки на главную есть название. */}
      <Box
        component="span"
        className={compact ? 'sr-only sm:not-sr-only' : undefined}
        sx={{ display: 'flex', flexDirection: 'column', lineHeight: 1, textTransform: 'uppercase', letterSpacing: '0.3px' }}
      >
        <Box component="span" sx={{ color: TOKENS.cta, fontWeight: 800, fontSize: '1.125rem' }}>
          {FIRST}
        </Box>
        {SECOND && (
          <Box component="span" sx={{ color: TOKENS.mute, fontWeight: 400, fontSize: '1.125rem', mt: '1px' }}>
            {SECOND}
          </Box>
        )}
      </Box>
    </Box>
  )
}
