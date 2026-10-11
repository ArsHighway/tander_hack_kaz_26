import { Alert, Box, Button, TextField, Typography } from '@mui/material'
import { IconArrowRight } from '@tabler/icons-react'
import { useState } from 'react'
import { navigate } from '../../../lib/router'
import { TOKENS } from '../../../theme'
import { FileDrop } from './FileDrop'
import { Kbd } from './Kbd'

type Props = {
  text: string
  onText: (text: string) => void
  onFill: () => void
  error: string | null
  busy: boolean
}

// Что модуль берёт из текста: наименование, числа с единицами, слова перечней КТРУ, общие сведения.
const GUIDE = [
  'Наименование: вид товара, модель или артикул',
  'Размеры, вес, объём, мощность с единицами измерения',
  'Материал, цвет, тип и назначение',
  'Производитель и страна происхождения',
]

/**
 * Шаг 1, он же главная: одно действие крупно — описать один товар. Описание из файла — строкой под полем; много
 * товаров — внизу ссылка на массовую загрузку с объяснением, чем она отличается.
 */
export function StepDescribe({ text, onText, onFill, error, busy }: Props) {
  const [reading, setReading] = useState(false)
  const ready = text.trim() !== '' && !busy && !reading
  return (
    <Box className="flex min-w-0 flex-col gap-6">
      <Box>
        {/* Ниже заголовка экрана «Новая СТЕ» (30) на ступень: 24, как заголовок шага 4. */}
        <Typography component="h2" sx={{ fontSize: { xs: '1.25rem', md: '1.5rem' }, lineHeight: 1.3, fontWeight: 700 }}>
          Опишите товар, карточку заполнит модуль
        </Typography>
        <Typography sx={{ mt: 0.5, color: TOKENS.ink2, fontSize: '1.0625rem', maxWidth: '72ch' }}>
          Модуль подберёт позицию КТРУ, заполнит характеристики и проверит карточку по правилам модерации портала.
        </Typography>
      </Box>
      {/* Как описать любой товар: что модуль ищет в тексте. Без примеров конкретных товаров — список подходит всем. */}
      {/* Памятка — рамкой без заливки: голубая заливка у поля ввода ниже, памятку с полем не спутать. */}
      <Box id="describe-hint" className="p-4" sx={{ border: `1px solid ${TOKENS.line}` }}>
        <Typography sx={{ fontWeight: 700, fontSize: '1rem' }}>Что написать в описании</Typography>
        <Box component="ol" className="m-0 mt-2 grid list-decimal gap-x-8 gap-y-1 pl-5 md:grid-flow-col md:grid-cols-2 md:grid-rows-2" sx={{ fontSize: '0.9375rem', color: TOKENS.ink2 }}>
          {GUIDE.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </Box>
      </Box>
      <Box className="flex flex-col gap-2">
        <Typography component="label" htmlFor="describe-text" sx={{ fontWeight: 600, fontSize: '1.0625rem' }}>
          Наименование и описание товара
        </Typography>
        {error && <Alert severity="error">{error}</Alert>}
        <FileDrop onText={onText} disabled={busy} onBusy={setReading}>
          <TextField
            id="describe-text"
            name="supplier-text"
            autoComplete="off"
            multiline
            minRows={7}
            fullWidth
            value={text}
            disabled={busy}
            onChange={(e) => onText(e.target.value)}
            slotProps={{ htmlInput: { 'aria-describedby': 'describe-hint' } }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && ready) {
                e.preventDefault()
                onFill()
              }
            }}
          />
        </FileDrop>
      </Box>
      <Box className="flex flex-wrap items-center gap-3">
        <Button variant="contained" size="large" disabled={!ready} onClick={onFill} aria-keyshortcuts="Control+Enter" sx={{ minHeight: 52, px: 3.5 }}>
          <span>{busy ? 'Заполняем…' : 'Заполнить карточку'}</span>
          <Box component="span" sx={{ ml: 1.5, display: { xs: 'none', sm: 'inline-flex' } }}>
            <Kbd inverse>Ctrl Enter</Kbd>
          </Box>
        </Button>
      </Box>
      {/* Много товаров — другой раздел: таблица целиком, а не одна карточка. Разница названа словами. */}
      <Box className="flex flex-wrap items-center gap-x-2 gap-y-1 pt-5" sx={{ borderTop: `1px solid ${TOKENS.line}`, fontSize: '0.9375rem', color: TOKENS.ink2 }}>
        <span>Нужно завести много товаров сразу? Загрузите таблицу Excel или CSV, модуль заполнит каждую строку.</span>
        <Button href="/batch" onClick={(e) => navigate(e, '/batch')} endIcon={<IconArrowRight size={18} stroke={1.5} aria-hidden />} sx={{ px: 1 }}>
          Массовая загрузка
        </Button>
      </Box>
    </Box>
  )
}
