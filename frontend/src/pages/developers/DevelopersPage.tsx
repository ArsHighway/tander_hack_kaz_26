import { Box, Paper, Tab, Tabs, Typography } from '@mui/material'
import { useState } from 'react'
import { Page } from '../../shell/Page'
import { MONO, TOKENS } from '../../theme'
import { answerReq, answerRes, autofillReq, autofillRes, checkReq, checkRes } from './examples'

type Method = { verb: 'GET' | 'POST'; path: string; what: string }

const METHODS: Method[] = [
  { verb: 'POST', path: '/api/v1/autofill', what: 'наименование и описание → кандидаты КТРУ, характеристики с источником в тексте, замечания, уточняющий вопрос' },
  { verb: 'POST', path: '/api/v1/autofill/{session_id}/answer', what: 'ответ на вопрос → сужение до конечного кода или шаги, если код не определить' },
  { verb: 'POST', path: '/api/v1/check', what: 'готовая карточка → замечания с обоснованием и ссылкой на правило модерации' },
  { verb: 'GET', path: '/api/v1/ktru/{code}', what: 'категория справочника или позиция КТРУ: название, путь, характеристики, допустимые значения, единицы' },
  { verb: 'POST', path: '/api/v1/batch', what: 'файл xlsx или csv в очередь: тот же разбор для каждой строки' },
  { verb: 'GET', path: '/api/v1/batch/{job_id}', what: 'ход обработки и результат по строкам; …/result.xlsx: файл с результатом' },
  { verb: 'GET', path: '/api/v1/metrics', what: 'качество на золотом наборе: метрика, значение, выборка, способ расчёта' },
]

const FLOW = [
  { who: 'Портал', text: 'Поставщик вводит наименование и описание в форме «Создание СТЕ».' },
  { who: 'Модуль', text: 'POST /api/v1/autofill: кандидаты КТРУ, характеристики, замечания и вопрос, если позиций несколько.' },
  { who: 'Поставщик', text: 'Отвечает на вопрос (…/answer) и правит поля карточки, которую модуль заполнил за него.' },
  { who: 'Модуль', text: 'POST /api/v1/check перед отправкой: что нарушает правила модерации и чем исправить.' },
  { who: 'Модератор', text: 'Видит заявку с теми же замечаниями модуля и принимает её или возвращает с готовым текстом.' },
]

const NOTES = [
  'Модуль ничего не пишет в данные портала: ответ содержит предложения, а решение по каждому полю принимает человек.',
  'Значение и единица попадают в ответ, только если они есть в перечне позиции справочника и в ОКЕИ; это проверяет код, а не модель.',
  'API без состояния. Сессия уточнения хранится час; на 404 «сессия заполнения не найдена или истекла» достаточно повторить /autofill.',
  'Ошибка приходит как JSON {"detail": "…"} с текстом для человека; 422 означает ошибки полей запроса.',
  'Поле degraded: ["llm"] значит, что характеристики извлечены только правилами; ["embeddings"] значит, что позиции искались без векторного индекса.',
  'Справочник подключается файлами (CATALOG_PATHS): под формат справочника организатора пишется загрузчик, контракт API не меняется.',
]

const PAIRS = [
  { id: 'autofill', label: 'Заполнение', req: autofillReq, res: autofillRes, path: 'POST /api/v1/autofill' },
  { id: 'answer', label: 'Ответ на вопрос', req: answerReq, res: answerRes, path: 'POST /api/v1/autofill/{session_id}/answer' },
  { id: 'check', label: 'Проверка', req: checkReq, res: checkRes, path: 'POST /api/v1/check' },
]

function Code({ title, children }: { title: string; children: string }) {
  return (
    <Box component="figure" className="min-w-0">
      <Typography component="figcaption" variant="body2" color="text.secondary" className="mb-1.5">
        {title}
      </Typography>
      <Box
        component="pre"
        tabIndex={0}
        aria-label={title}
        className="max-h-[420px] overflow-auto p-4 text-[0.75rem] leading-relaxed"
        sx={{ bgcolor: 'background.default', border: 1, borderColor: 'divider', fontFamily: MONO }}
      >
        {children}
      </Box>
    </Box>
  )
}

const LINKS = [
  { href: '/api/docs', label: 'Интерактивная документация (Swagger)' },
  { href: '/api/openapi.json', label: 'Контракт OpenAPI' },
  { href: '/bpmn/module.svg', label: 'BPMN-схема' },
]

export function DevelopersPage() {
  const [tab, setTab] = useState(PAIRS[0].id)
  const pair = PAIRS.find((p) => p.id === tab) ?? PAIRS[0]
  return (
    <Page
      title="API для Портала поставщиков"
      description="Как встроить модуль в форму «Создание СТЕ»: портал отправляет текст поставщика и показывает ответ модуля, решение остаётся за человеком."
    >
      <Box className="flex flex-col gap-6">
        <Paper variant="outlined" className="p-5 md:p-6">
          <Typography variant="h6" component="h2">
            Поток
          </Typography>
          <Typography variant="body2" color="text.secondary">
            BPMN-схема процесса с дорожками портала, модуля и модератора лежит по ссылке ниже.
          </Typography>
          <Box component="ol" className="mt-4 grid gap-4 md:grid-cols-5">
            {FLOW.map((s, i) => (
              <Box component="li" key={i} className="flex flex-col gap-1.5">
                {/* Шаг процесса — кружок из UI-кита портала, как в полосе шагов мастера. */}
                <Box className="flex items-center gap-2">
                  <Box
                    aria-hidden
                    sx={{ width: 28, height: 28, borderRadius: '50%', bgcolor: TOKENS.cta, color: '#ffffff', display: 'grid', placeItems: 'center', fontSize: '0.9375rem', flex: 'none' }}
                  >
                    {i + 1}
                  </Box>
                  <Typography sx={{ fontSize: '0.9375rem', fontWeight: 700 }}>{s.who}</Typography>
                </Box>
                <Typography variant="body2">{s.text}</Typography>
              </Box>
            ))}
          </Box>
          <Box className="mt-5 flex flex-wrap gap-x-5 gap-y-2">
            {LINKS.map((l) => (
              <Typography key={l.href} component="a" href={l.href} variant="body2" color="primary" sx={{ fontWeight: 600 }}>
                {l.label}
              </Typography>
            ))}
          </Box>
        </Paper>

        <Paper variant="outlined" className="overflow-hidden">
          <Typography variant="h6" component="h2" className="px-5 pt-5 pb-2 md:px-6">
            Методы
          </Typography>
          <Box component="ul">
            {METHODS.map((m) => (
              <Box
                component="li"
                key={m.verb + m.path}
                className="grid gap-1 px-5 py-3 md:grid-cols-[minmax(0,340px)_minmax(0,1fr)] md:gap-6 md:px-6"
                sx={{ borderTop: 1, borderColor: 'divider' }}
              >
                <Box component="code" translate="no" sx={{ fontFamily: MONO, fontSize: '0.875rem' }}>
                  {m.verb} {m.path}
                </Box>
                <Typography variant="body2" color="text.secondary">
                  {m.what}
                </Typography>
              </Box>
            ))}
          </Box>
        </Paper>

        <Paper variant="outlined" className="p-5 md:p-6">
          <Typography variant="h6" component="h2">
            Примеры
          </Typography>
          <Tabs value={tab} onChange={(_, v: string) => setTab(v)} aria-label="Примеры запросов">
            {PAIRS.map((p) => (
              <Tab key={p.id} value={p.id} label={p.label} />
            ))}
          </Tabs>
          <Box className="mt-4 grid gap-4 lg:grid-cols-2">
            <Code title={`Запрос: ${pair.path}`}>{pair.req}</Code>
            <Code title="Ответ">{pair.res}</Code>
          </Box>
        </Paper>

        <Paper variant="outlined" className="p-5 md:p-6">
          <Typography variant="h6" component="h2">
            Что важно знать
          </Typography>
          <Box component="ul" className="mt-3 flex list-disc flex-col gap-2 pl-5">
            {NOTES.map((n) => (
              <Typography component="li" variant="body2" key={n}>
                {n}
              </Typography>
            ))}
          </Box>
        </Paper>
      </Box>
    </Page>
  )
}
