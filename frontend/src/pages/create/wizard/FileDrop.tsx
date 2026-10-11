import { Alert, Box, Button, Typography } from '@mui/material'
import { IconFileText } from '@tabler/icons-react'
import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { api } from '../../../api/v1.ts'
import { pendingFile } from '../../../lib/pendingFile.ts'
import { go } from '../../../lib/router'
import { TOKENS } from '../../../theme'
import { checkExtract, extractError, fitText, goodsCount } from './extract.ts'

type Props = {
  /** Текст одного товара из файла: в поле шага 1, заполнение — по кнопке человека. */
  onText: (text: string) => void
  disabled: boolean
  /** Файл читается: шаг 1 не заполняет карточку, пока текст не пришёл. */
  onBusy?: (reading: boolean) => void
  /** Поле текста шага 1: на него можно перетащить файл, рамка подсвечивается синим. */
  children: ReactNode
}

type State =
  | { kind: 'idle' }
  | { kind: 'reading'; name: string }
  | { kind: 'one'; name: string; cut: boolean }
  | { kind: 'many'; file: File; items: number }
  | { kind: 'error'; name: string; message: string }

const ACCEPT = '.docx,.pdf,.xlsx,.xlsm,.xls,.xlsb,.ods,.csv,.tsv,.txt'

/**
 * Описание одного товара из файла (Word, PDF, текст; таблица с одним товаром): файл перетаскивают на поле текста или
 * выбирают строкой под ним. Файл с несколькими товарами ведёт в массовую загрузку.
 */
export function FileDrop({ onText, disabled, onBusy, children }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [state, setState] = useState<State>({ kind: 'idle' })
  const [over, setOver] = useState(false)
  const busy = disabled || state.kind === 'reading'
  // Человек ушёл с шага 1 (нажал «Заполнить карточку»), пока файл читался: поздний текст не нужен.
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  useEffect(() => onBusy?.(state.kind === 'reading'), [state.kind, onBusy])

  const read = async (file: File | undefined) => {
    if (!file || busy) return
    const problem = checkExtract(file)
    if (problem) return setState({ kind: 'error', name: file.name, message: problem })
    setState({ kind: 'reading', name: file.name })
    try {
      const out = await api.extract(file)
      if (!alive.current) return
      if (out.items > 1) return setState({ kind: 'many', file, items: out.items })
      // Длиннее пределов модуля заполнение не примет: текст обрезается, и человек это видит.
      const fitted = fitText(out.text)
      onText(fitted.text)
      setState({ kind: 'one', name: file.name, cut: fitted.cut })
    } catch (e) {
      if (alive.current) setState({ kind: 'error', name: file.name, message: extractError(e) })
    }
  }

  const drop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    void read(e.dataTransfer.files[0])
  }

  const picker = (
    <input
      ref={input}
      type="file"
      accept={ACCEPT}
      hidden
      aria-label="Файл с описанием товара"
      onChange={(e) => {
        const file = e.target.files?.[0]
        e.target.value = '' // тот же файл можно выбрать снова, и после отказа
        void read(file)
      }}
    />
  )
  const status = (
    <Box role="status" aria-live="polite">
      {state.kind === 'one' && (
        <Typography sx={{ fontSize: '0.875rem', color: TOKENS.ink2, overflowWrap: 'anywhere' }}>
          Текст взят из «{state.name}» и стоит в поле{state.cut ? ' (обрезан до 20 000 знаков: дальше модуль не читает)' : ''}. Проверьте его и нажмите
          «Заполнить карточку».
        </Typography>
      )}
      {state.kind === 'error' && (
        <Alert severity="error" sx={{ overflowWrap: 'anywhere' }}>
          «{state.name}»: {state.message}
        </Alert>
      )}
      {state.kind === 'many' && (
        <Alert severity="info" sx={{ overflowWrap: 'anywhere' }}>
          В «{state.file.name}» {goodsCount(state.items)}. Одной карточкой это не заполнить: в массовой
          загрузке модуль заполнит каждую строку.
          {/* Кнопка под текстом, а не сбоку: на телефоне длинная подпись сбоку рвётся по буквам. */}
          <Box sx={{ mt: 1 }}>
            <Button
              variant="outlined"
              size="small"
              onClick={() => {
                pendingFile.put(state.file)
                go('/batch')
              }}
            >
              Открыть массовую загрузку
            </Button>
          </Box>
        </Alert>
      )}
    </Box>
  )

  return (
    <Box className="flex flex-col gap-2">
      <Box
        onDragOver={(e) => {
          e.preventDefault()
          if (!busy) setOver(true)
        }}
        onDragLeave={(e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && setOver(false)}
        onDrop={drop}
        sx={over ? { outline: `2px dashed ${TOKENS.acc}`, outlineOffset: 2, bgcolor: TOKENS.accSoft } : undefined}
      >
        {children}
      </Box>
      {/* Файл — запасной путь для описания одного товара: строка под полем, а не вторая главная зона экрана. */}
      <Box className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Button
          disabled={busy}
          onClick={() => input.current?.click()}
          startIcon={<IconFileText size={20} stroke={1.5} aria-hidden />}
          sx={{ px: 1, ml: -1 }}
        >
          {state.kind === 'reading' ? 'Читаем файл…' : 'Вставить описание из файла'}
        </Button>
        <Typography component="span" sx={{ fontSize: '0.875rem', color: TOKENS.mute }}>
          Word, PDF или текст до 10 МБ; можно перетащить на поле
        </Typography>
        {picker}
      </Box>
      {status}
    </Box>
  )
}
