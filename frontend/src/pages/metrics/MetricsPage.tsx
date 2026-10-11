import { Alert, AlertTitle, Box, Link, Paper, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material'
import { useEffect, useState } from 'react'
import { ApiError, api, type AuditExample, type HoldoutOut, type MetricRow, type MetricsOut, type PortalReport } from '../../api/v1.ts'
import { decimal, plural } from '../../lib/format'
import { Page } from '../../shell/Page'
import { formatMetric, portalHeadline, splitRules } from './metricsView.ts'

const date = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })

function MetricsTable({ rows, caption }: { rows: MetricRow[]; caption: string }) {
  return (
    <TableContainer>
      <Table size="small" aria-label={caption}>
        <TableHead>
          <TableRow>
            <TableCell>Метрика</TableCell>
            <TableCell align="right">Значение</TableCell>
            <TableCell align="right">Выборка</TableCell>
            <TableCell>Как считали</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.key}>
              <TableCell>{r.title}</TableCell>
              <TableCell align="right" sx={{ fontWeight: 600, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                {formatMetric(r.key, r.value)}
              </TableCell>
              <TableCell align="right" sx={{ color: 'text.secondary', fontVariantNumeric: 'tabular-nums' }}>
                {decimal(r.n, 0)}
              </TableCell>
              <TableCell sx={{ color: 'text.secondary', fontSize: '0.875rem' }}>{r.how}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

function PortalLink({ example }: { example: AuditExample }) {
  return (
    <Link href={example.url} target="_blank" rel="noreferrer" underline="always" sx={{ position: 'relative' }}>
      {example.name}
      <span className="sr-only"> (карточка на портале, откроется в новой вкладке)</span>
    </Link>
  )
}

function PortalSection({ report }: { report: PortalReport }) {
  const { slips, other } = splitRules(report)
  return (
    <>
      <Paper variant="outlined" className="overflow-hidden">
        <Box className="px-5 pt-5 pb-3 md:px-6">
          <Typography variant="h6" component="h2">
            Живые карточки портала: модерация
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {decimal(report.cards, 0)} СТЕ из {decimal(report.categories, 0)} категорий каталога, собраны с публичного API zakupki.mos.ru,{' '}
            {date.format(new Date(report.created_at))}
          </Typography>
          <Box className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <Typography sx={{ fontSize: '1.875rem', fontWeight: 600 }} color="primary">
              {formatMetric('share', report.audit.share)}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {portalHeadline(report)}.
            </Typography>
          </Box>
        </Box>
        <TableContainer>
          <Table size="small" aria-label="Нарушения правил модерации в живых карточках портала">
            <TableHead>
              <TableRow>
                <TableCell>Правило модерации</TableCell>
                <TableCell align="right">Карточек</TableCell>
                <TableCell align="right">Доля</TableCell>
                <TableCell>Пример</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {other.map((r) => (
                <TableRow key={r.rule}>
                  <TableCell>{r.rule}</TableCell>
                  <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
                    {decimal(r.cards, 0)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                    {formatMetric('share', r.share)}
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.875rem' }}>
                    {r.examples[0] && (
                      <>
                        <PortalLink example={r.examples[0]} />
                        <Typography variant="caption" color="text.secondary" component="span" className="block">
                          {r.examples[0].message}
                        </Typography>
                      </>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>

      {slips && (
        <Paper variant="outlined" className="p-5 md:p-6">
          <Typography variant="h6" component="h2">
            Ошибки ввода в живых карточках
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {slips.cards} {plural(slips.cards, 'карточка', 'карточки', 'карточек')}: число в характеристике расходится с наименованием так, как
            ошибаются при вводе. Перепутана единица, опечатка в числе или отрицательная величина.
          </Typography>
          <Box component="ol" className="mt-3">
            {slips.examples.map((e) => (
              <Box component="li" key={`${e.id}-${e.field}`} className="py-2.5" sx={{ borderTop: 1, borderColor: 'divider' }}>
                <PortalLink example={e} />
                <Typography component="span" variant="caption" color="text.secondary" className="ml-2" translate="no">
                  СТЕ {e.id}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {e.message}
                </Typography>
              </Box>
            ))}
          </Box>
        </Paper>
      )}

      <Paper variant="outlined" className="overflow-hidden">
        <Box className="px-5 pt-5 pb-2 md:px-6">
          <Typography variant="h6" component="h2">
            Заполнение на живых карточках
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Значения, которые модуль извлёк правилами из наименования и описания, против введённых поставщиком. Значение поставщика не эталон:
            в нём бывают те же ошибки.
          </Typography>
        </Box>
        <MetricsTable rows={report.fill} caption="Заполнение характеристик на живых карточках портала" />
      </Paper>
    </>
  )
}

function HoldoutSection({ report }: { report: HoldoutOut }) {
  return (
    <Paper variant="outlined" className="overflow-hidden">
      <Box className="px-5 pt-5 pb-2 md:px-6">
        <Typography variant="h6" component="h2">
          Отложенная выборка СТЕ портала
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {decimal(report.cases, 0)} СТЕ из разных категорий, вынутые вместе с двойниками из справочника и из похожих СТЕ, справочник{' '}
          {report.catalog}, {report.llm ? `модель ${report.llm}` : 'без LLM и векторов (нижняя оценка)'}, {date.format(new Date(report.created_at))}
        </Typography>
      </Box>
      <MetricsTable rows={report.rows} caption="Качество на отложенной выборке СТЕ портала" />
    </Paper>
  )
}

export function MetricsPage() {
  const [metrics, setMetrics] = useState<MetricsOut | null | undefined>(undefined)
  const [holdout, setHoldout] = useState<HoldoutOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [portal, setPortal] = useState<PortalReport | null | undefined>(undefined)

  useEffect(() => {
    api
      .metrics()
      .then(setMetrics)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.detail : 'не удалось загрузить метрики'))
    api
      .portalReport()
      .then(setPortal)
      .catch(() => setPortal(null))
    api
      .holdout()
      .then(setHoldout)
      .catch(() => setHoldout(null))
  }, [])

  const synthetic = metrics?.catalog_source.includes('synthetic') ?? false
  return (
    <Page
      title="Метрики"
      description="Качество модуля на золотом наборе и на живых карточках Портала поставщиков: подбор кода КТРУ, заполнение характеристик, поиск ошибок, скорость. Числа берутся из последнего прогона как есть."
    >
      <Box className="flex flex-col gap-6">
        {holdout && <HoldoutSection report={holdout} />}
        {error && <Alert severity="error">{error}</Alert>}
        {metrics === undefined && !error && <Skeleton variant="rounded" height={260} />}
        {metrics === null && (
          <Alert severity="info">
            <AlertTitle sx={{ fontWeight: 600 }}>Оценка ещё не запускалась</AlertTitle>
            Запустите в контейнере api: python -m app.tools.evaluate. Результат появится здесь.
          </Alert>
        )}
        {metrics && (
          <>
            {synthetic && (
              <Alert severity="warning">
                <AlertTitle sx={{ fontWeight: 600 }}>Демо-справочник</AlertTitle>
                Числа показывают работу конвейера на синтетическом справочнике, а не качество на данных организаторов. Золотой набор собран из того
                же справочника, поэтому оценка оптимистична.
              </Alert>
            )}
            <Paper variant="outlined" className="overflow-hidden">
              <Box className="px-5 pt-5 pb-2 md:px-6">
                <Typography variant="h6" component="h2">
                  {synthetic ? 'Золотой набор на демо-справочнике' : 'Золотой набор'}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {date.format(new Date(metrics.created_at))}, справочник {metrics.catalog_version}, {metrics.model}
                </Typography>
              </Box>
              <MetricsTable rows={metrics.rows} caption="Метрики качества модуля" />
            </Paper>
          </>
        )}
        {portal && <PortalSection report={portal} />}
      </Box>
    </Page>
  )
}
