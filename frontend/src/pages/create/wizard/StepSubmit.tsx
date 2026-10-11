import { Alert, AlertTitle, Box, Button, Link, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Typography } from '@mui/material'
import { useState } from 'react'
import type { AutofillOut, Issue, Submission } from '../../../api/v1.ts'
import { PhotoThumb } from '../../../card/PhotoThumb'
import { go } from '../../../lib/router'
import { MONO, TOKENS } from '../../../theme'
import { ktruText } from '../chainView.ts'
import { fieldLabel } from '../createModel.ts'
import { categoryPath, codeWithName, type FieldState } from '../formModel.ts'
import { ktruValue } from './ktruFormat.ts'
import { BLOCKED_ID, submitState, type SubmitReport } from './submit.ts'
import type { PreviewView } from './portalPreview.ts'
import { PortalPreview } from './PortalPreviewPanel'
import { finalKtru, ktruSummary, portalLink } from './summary.ts'

type Props = {
  out: AutofillOut
  name: string
  fields: FieldState[]
  /** Замечания последней проверки `/check`; null — проверка идёт, 'failed' — модуль не ответил. */
  issues: Issue[] | null | 'failed'
  /** Повторить проверку после сбоя. */
  onRetry: () => void
  /** Та же карточка в разметке страницы СТЕ портала. */
  preview: PreviewView
  photoId: number | null
  photoUploading: boolean
  busy: boolean
  revise: boolean
  onSubmit: () => void
  /** «Отправить», когда отправить нельзя: перейти к тому, что мешает. */
  onBlocked: (report: SubmitReport) => void
  /** Ссылка из замечания: шаг 4 и фокус на поле. */
  onGoToField: (name: string) => void
  onBack: () => void
}

/** Моноширинным — только сами коды, а не «В КТРУ нет» и не название категории. */
const CODE = /^[\d.,\s-]+$/
const FIELD_PREFIX = 'characteristics.'

function FieldLink({ name, onGo }: { name: string; onGo: (name: string) => void }) {
  return (
    <Link component="button" type="button" onClick={() => onGo(name)} sx={{ font: 'inherit', verticalAlign: 'baseline', textAlign: 'left' }}>
      {name}
    </Link>
  )
}

/** «Код · название»: код моноширинным, название — основным шрифтом. */
function CodeValue({ value }: { value: string }) {
  const at = value.indexOf(' · ')
  if (at < 0 || !CODE.test(value.slice(0, at))) return <>{value}</>
  return (
    <>
      <Box component="span" sx={{ fontFamily: MONO }}>
        {value.slice(0, at)}
      </Box>
      {value.slice(at)}
    </>
  )
}

/** КТРУ — основной код, КПГЗ — дополнение с явным источником связи. */
export function Codes({ out }: { out: AutofillOut }) {
  const chain = out.chain
  const final = out.ktru.candidates.find((c) => c.code === out.ktru.final_code)
  // Итоговая позиция КТРУ — кодом и названием из ответа модуля: «31.01.10.000-00000005 · Стол письменный».
  const ktruFinal = chain?.ktru?.candidates.find((c) => c.code === chain.ktru?.final_code)
  const ktru = chain
    ? chain.ktru?.final_code
      ? codeWithName(chain.ktru.final_code, ktruFinal?.name)
      : ktruText(chain).value
    : finalKtru(out)
      ? codeWithName(finalKtru(out)!, final?.name)
      : 'не определён'
  const categoryName = chain?.category_name ?? final?.name ?? null
  const path = categoryPath(chain?.path, categoryName)
  const rows: [string, string | null][] = [
    ['КТРУ', ktru],
    ['КПГЗ', portalLink(chain)],
    ['Категория', categoryName && path ? `${path} / ${categoryName}` : categoryName],
    ['ОКПД2', chain?.okpd2 ?? null],
    ['СПГЗ', chain?.spgz.length ? chain.spgz.join(', ') : null],
  ]
  return (
    <Box component="dl" sx={{ m: 0, display: 'grid', gridTemplateColumns: 'max-content minmax(0,1fr)', columnGap: 2, rowGap: 0.75 }}>
      {rows
        .filter(([, v]) => v)
        .map(([k, v]) => (
          <Box key={k} sx={{ display: 'contents' }}>
            <Box component="dt" sx={{ color: TOKENS.mute, fontSize: '0.9375rem' }}>
              {k}
            </Box>
            <Box component="dd" sx={{ m: 0, fontSize: '0.9375rem', fontFamily: CODE.test(v ?? '') ? MONO : undefined, overflowWrap: 'anywhere' }}>
              <CodeValue value={v ?? ''} />
            </Box>
          </Box>
        ))}
    </Box>
  )
}

/** Шаг 5: сводка карточки в формате КТРУ и итог проверки модерации перед отправкой. */
export function StepSubmit({ out, name, fields, issues, onRetry, preview, photoId, photoUploading, busy, revise, onSubmit, onBlocked, onGoToField, onBack }: Props) {
  const [view, setView] = useState<'summary' | 'portal'>('summary')
  const filled = fields.filter((f) => f.value.trim())
  const report = submitState(issues, fields)
  const summary = ktruSummary(out, fields, issues)
  const state = report.state
  const shown = new Set(fields.map((f) => f.name))
  // Замечание к полю карточки — ссылкой на поле, как «Осталось заполнить» на шаге 4.
  const line = (i: Issue) => {
    const f = i.field.startsWith(FIELD_PREFIX) ? i.field.slice(FIELD_PREFIX.length) : null
    return (
      <div key={`${i.field}-${i.rule}`}>
        {f && shown.has(f) ? <FieldLink name={f} onGo={onGoToField} /> : fieldLabel(i.field)}: {i.message}
      </div>
    )
  }
  return (
    <Box className="flex flex-col gap-5">
      <Box>
        <Typography component="h2" sx={{ fontSize: '1.25rem', fontWeight: 600 }}>
          Проверка перед отправкой
        </Typography>
        <Typography sx={{ mt: 0.75, color: TOKENS.ink2 }}>Так карточка уйдёт модератору. Проверьте значения и замечания к ним.</Typography>
      </Box>
      <Box component="section" aria-label="Сводка соответствия КТРУ" sx={{ p: 2, border: `1px solid ${TOKENS.line}`, overflowWrap: 'anywhere' }}>
        <Typography component="h3" sx={{ fontSize: '1.0625rem', fontWeight: 600 }}>{summary.title}</Typography>
        <Typography sx={{ mt: 0.5 }}>{summary.text}</Typography>
        <Typography sx={{ mt: 0.5, fontSize: '0.875rem', color: TOKENS.ink2 }}>Заполненность не означает соответствие: значения с замечаниями нужно проверить.</Typography>
        {summary.verified && summary.outside.length > 0 && <Box sx={{ mt: 1, color: 'error.main' }}>Вне КТРУ: {summary.outside.map((n, i) => <span key={n}>{i > 0 && ', '}<FieldLink name={n} onGo={onGoToField} /></span>)}</Box>}
        {summary.missing.length > 0 && <Box sx={{ mt: 1 }}>Осталось заполнить: {summary.missing.map((n, i) => <span key={n}>{i > 0 && ', '}<FieldLink name={n} onGo={onGoToField} /></span>)}</Box>}
        {summary.added > 0 && <Typography sx={{ mt: 1, fontSize: '0.875rem', color: TOKENS.ink2 }}>Новые характеристики: предложения вне КТРУ, их подтверждает модератор.</Typography>}
      </Box>
      <Tabs value={view} onChange={(_, v: 'summary' | 'portal') => setView(v)} aria-label="Вид карточки" sx={{ minHeight: 40, '@media (pointer: coarse)': { minHeight: 44 }, borderBottom: `1px solid ${TOKENS.line}` }}>
        <Tab value="summary" label="Сводка" id="submit-tab-summary" aria-controls="submit-panel" sx={{ minHeight: 40, '@media (pointer: coarse)': { minHeight: 44 }, px: 1.5, minWidth: 0 }} />
        <Tab value="portal" label="Как на портале" id="submit-tab-portal" aria-controls="submit-panel" sx={{ minHeight: 40, '@media (pointer: coarse)': { minHeight: 44 }, px: 1.5, minWidth: 0 }} />
      </Tabs>
      <Box role="tabpanel" id="submit-panel" aria-labelledby={`submit-tab-${view}`} className="flex flex-col gap-5">
      {view === 'portal' ? (
        <PortalPreview view={preview} />
      ) : (
        <>
          <Box>
            <Box className="mb-1 flex items-start gap-3">
              {photoId !== null && <PhotoThumb key={photoId} photoId={photoId} size={56} />}
              <Typography sx={{ fontWeight: 600, fontSize: '1.0625rem', overflowWrap: 'anywhere' }}>{name}</Typography>
            </Box>
            <Codes out={out} />
          </Box>
          {/* Ширины столбцов заданы: на телефоне слова переносятся целиком, а единица встаёт рядом со значением. */}
          <Table
            size="small"
            aria-label="Характеристики карточки"
            sx={{ tableLayout: 'fixed', '& th, & td': { px: { xs: 1, sm: 2 }, overflowWrap: 'break-word' } }}
          >
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: { xs: '45%', sm: '40%' }, color: TOKENS.mute, fontWeight: 500 }}>Характеристика</TableCell>
                <TableCell sx={{ color: TOKENS.mute, fontWeight: 500 }}>Значение</TableCell>
                <TableCell sx={{ width: '22%', display: { xs: 'none', sm: 'table-cell' }, color: TOKENS.mute, fontWeight: 500 }}>Единица</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filled.map((f) => {
                const unit = f.unitName ?? f.unit ?? ''
                return (
                  <TableRow key={f.name}>
                    <TableCell>{f.name}</TableCell>
                    <TableCell>
                      {ktruValue(f.value)}
                      {unit && (
                        <Box component="span" sx={{ display: { sm: 'none' }, color: TOKENS.ink2 }}>
                          {' '}
                          {unit}
                        </Box>
                      )}
                    </TableCell>
                    <TableCell sx={{ display: { xs: 'none', sm: 'table-cell' }, color: TOKENS.ink2 }}>{unit}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </>
      )}
      </Box>
      {state === 'checking' ? (
        <Typography sx={{ color: TOKENS.ink2 }} role="status">
          Проверяем карточку по правилам модерации…
        </Typography>
      ) : state === 'failed' ? (
        <Alert
          severity="error"
          action={
            <Button color="inherit" size="small" onClick={onRetry}>
              Повторить проверку
            </Button>
          }
        >
          Проверить карточку не удалось: модуль не ответил. Отправить можно после проверки.
        </Alert>
      ) : (
        <>
          {state === 'blocked' && (
            // Фокус сюда ставит «Отправить», когда мешает не поле (наименование, код).
            <Alert severity="error" id={BLOCKED_ID} tabIndex={-1}>
              <AlertTitle sx={{ fontWeight: 600 }}>Отправить пока нельзя</AlertTitle>
              {report.missing.length > 0 && (
                <div>
                  Заполните:{' '}
                  {report.missing.map((n, k) => (
                    <span key={n}>
                      {k > 0 && ', '}
                      <FieldLink name={n} onGo={onGoToField} />
                    </span>
                  ))}
                </div>
              )}
              {report.blocks.map(line)}
            </Alert>
          )}
          {report.warns.length > 0 && (
            <Alert severity="warning">
              <AlertTitle sx={{ fontWeight: 600 }}>Проверьте перед отправкой</AlertTitle>
              {report.warns.map(line)}
              {state === 'warn' && <Box sx={{ mt: 0.5 }}>Если всё верно, отправляйте.</Box>}
            </Alert>
          )}
          {state === 'ok' && (
            <Alert severity="success" sx={{ fontWeight: 600 }}>
              Правила модерации портала пройдены
            </Alert>
          )}
          {report.notes.length > 0 && (
            <Box sx={{ fontSize: '0.9375rem', color: TOKENS.ink2 }}>
              <Typography component="h3" sx={{ fontSize: '0.9375rem', fontWeight: 600 }}>
                К сведению
              </Typography>
              {report.notes.map(line)}
            </Box>
          )}
        </>
      )}
      <Box className="flex flex-wrap items-center gap-2.5">
        {/* Отправить нельзя — кнопка не молчит: ведёт к первому полю, которое мешает (`onBlocked`). */}
        <Button
          variant="contained"
          disabled={busy || photoUploading || state === 'checking' || state === 'failed'}
          aria-describedby={state === 'blocked' ? BLOCKED_ID : undefined}
          onClick={() => (state === 'blocked' ? onBlocked(report) : onSubmit())}
        >
          {busy ? 'Отправляем…' : revise ? 'Отправить исправленную заявку' : 'Отправить на модерацию'}
        </Button>
        <Button color="inherit" disabled={busy} onClick={onBack} sx={{ color: TOKENS.ink2 }}>
          Вернуться к характеристикам
        </Button>
      </Box>
    </Box>
  )
}

/** После отправки: номер заявки и что дальше. */
export function Sent({ submission, revise, onAgain }: { submission: Submission; revise: boolean; onAgain: () => void }) {
  return (
    <Box className="flex flex-col gap-4">
      <Alert severity="success">
        <AlertTitle sx={{ fontWeight: 600 }}>
          {revise ? 'Исправленная заявка' : 'Заявка'} № {submission.id} у модератора
        </AlertTitle>
        {submission.card.name}
      </Alert>
      <Box className="flex flex-wrap items-center gap-2.5">
        <Button variant="contained" onClick={() => go('/my')}>
          Открыть «Мои заявки»
        </Button>
        <Button variant="outlined" onClick={onAgain}>
          Создать ещё одну СТЕ
        </Button>
      </Box>
    </Box>
  )
}
