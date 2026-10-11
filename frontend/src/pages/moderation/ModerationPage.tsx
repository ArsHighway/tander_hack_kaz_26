import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Paper,
  Skeleton,
  TextField,
  Typography,
} from '@mui/material'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api, type QueuedSubmission, type Submission, type SubmissionView } from '../../api/v1.ts'
import { CharacteristicFields, KtruField, NameField } from '../../card/CardFields'
import { NoPhoto, PhotoThumb } from '../../card/PhotoThumb'
import { SourceText } from '../../card/SourceText'
import { NBSP, plural } from '../../lib/format'
import { go } from '../../lib/router'
import { Page } from '../../shell/Page'
import { StatusMark, type StatusTone } from '../../shell/StatusMark'
import { TOKENS } from '../../theme'
import { cardCodes, categoryPath, codeWithName, issuesByField, submissionFields } from '../create/formModel.ts'
import { LlmSwitch } from './LlmSwitch.tsx'
import { cleanPending, issueText, returnNote, selectedId, splitOutsideKtru } from './moderationModel.ts'
import { OutsideKtruBlock } from './OutsideKtruBlock.tsx'

// Блок «Вне КТРУ — подтвердите» включается при сборке: VITE_MODERATION_OUTSIDE_KTRU=1. Данные о новых свойствах
// (имя по виду портала, примечание «предложение: вне КТРУ») приходят от бэкенда с B15; до того — выключен.
const OUTSIDE_KTRU_BLOCK = import.meta.env.VITE_MODERATION_OUTSIDE_KTRU === '1'

type Named = { name: string; path: string[] }

// Название позиции по коду (`GET /api/v1/ktru/{code}`: категория справочника или позиция КТРУ) —
// один запрос на код за открытие страницы; сбой — без названия, следующее открытие спросит снова.
const named = new Map<string, Promise<Named | null>>()

function lookup(code: string): Promise<Named | null> {
  let found = named.get(code)
  if (!found) {
    found = api.ktru(code).then(
      (p) => ({ name: p.name, path: p.path ?? [] }),
      () => {
        named.delete(code)
        return null
      },
    )
    named.set(code, found)
  }
  return found
}

function usePosition(code: string | null): Named | null {
  const [found, setFound] = useState<{ code: string; value: Named | null } | null>(null)
  useEffect(() => {
    if (!code) return
    let cancelled = false
    lookup(code).then((value) => !cancelled && setFound({ code, value }))
    return () => {
      cancelled = true
    }
  }, [code])
  return found && found.code === code ? found.value : null
}

// Статус портала: кружок и капс; подпись тёмным оттенком, на выделенной строке тоже 4,5:1.
const STATUS: Record<Submission['status'], { text: string; tone: StatusTone }> = {
  pending: { text: 'ждёт решения', tone: 'wait' },
  accepted: { text: 'принята', tone: 'ok' },
  returned: { text: 'возвращена', tone: 'back' },
}

const time = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail : fallback)

function Decision({ view, onDone }: { view: SubmissionView; onDone: () => void }) {
  const [note, setNote] = useState(() => returnNote(view.check.issues))
  const [busy, setBusy] = useState<'accept' | 'return' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const blocked = view.check.issues.some((i) => i.severity === 'block')

  if (view.status !== 'pending') {
    return (
      <Alert severity={view.status === 'accepted' ? 'success' : 'warning'}>
        <AlertTitle sx={{ fontWeight: 600 }}>{view.status === 'accepted' ? 'Заявка принята' : 'Заявка возвращена поставщику'}</AlertTitle>
        {view.decision_note && <span className="whitespace-pre-line">{view.decision_note}</span>}
      </Alert>
    )
  }

  const decide = async (decision: 'accept' | 'return') => {
    setBusy(decision)
    setError(null)
    try {
      await api.decide(view.id, { decision, note: decision === 'return' ? note : null })
      onDone()
    } catch (e) {
      setError(errorText(e, 'не удалось сохранить решение'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Box className="flex flex-col gap-3">
      {error && <Alert severity="error">{error}</Alert>}
      {view.decision_note && (
        <Alert severity="info">
          <AlertTitle sx={{ fontWeight: 600 }}>Поставщик исправил заявку после возврата</AlertTitle>
          Прежние замечания: <span className="whitespace-pre-line">{view.decision_note}</span>
        </Alert>
      )}
      {!view.triage.clean && (
        <Typography sx={{ fontSize: '0.9375rem', color: 'text.secondary' }}>Почему не в один клик: {view.triage.reasons.join('; ')}</Typography>
      )}
      {/* Подсказку чужих СТЕ поставщик принимает сам кнопкой «Принять»; у поля подпись «Принято из подсказки». */}
      {view.triage.hinted > 0 && (
        <Typography sx={{ fontSize: '0.9375rem', color: 'text.secondary' }}>
          {`Из подсказок СТЕ портала поставщик принял: ${view.triage.hinted} ${plural(view.triage.hinted, 'значение', 'значения', 'значений')}.`}
        </Typography>
      )}
      <TextField
        label="Текст возврата поставщику"
        helperText={
          blocked ? 'Модуль подготовил текст по найденным нарушениям, его можно править' : 'Блокирующих нарушений модуль не нашёл'
        }
        multiline
        minRows={3}
        fullWidth
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <Box className="flex flex-wrap justify-end gap-2">
        <Button variant={blocked ? 'contained' : 'outlined'} disabled={busy !== null || !note.trim()} onClick={() => decide('return')}>
          {busy === 'return' ? 'Возвращаем…' : 'Вернуть с замечаниями'}
        </Button>
        <Button variant={blocked ? 'outlined' : 'contained'} disabled={busy !== null} onClick={() => decide('accept')}>
          {busy === 'accept' ? 'Принимаем…' : 'Одобрить'}
        </Button>
      </Box>
    </Box>
  )
}

export function ModerationPage() {
  const [queue, setQueue] = useState<QueuedSubmission[] | null>(null)
  const [current, setCurrent] = useState<number | null>(() => selectedId(window.location.search))
  const [view, setView] = useState<SubmissionView | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Перечитать очередь и заявку после решения: меняется ключ, эффекты ниже загружают их заново.
  const [reload, setReload] = useState(0)
  // После «Одобрить N»: плашка итога получает фокус, если кнопки «Одобрить чистые» больше нет.
  const acceptedRef = useRef<HTMLDivElement>(null)
  const focusAccepted = useRef(false)

  useEffect(() => {
    let cancelled = false
    api
      .submissions()
      .then((items) => {
        if (cancelled) return
        setQueue(items)
        setCurrent((c) => c ?? items.find((s) => s.status === 'pending')?.id ?? items[0]?.id ?? null)
        if (focusAccepted.current) {
          focusAccepted.current = false
          if (cleanPending(items).length === 0) acceptedRef.current?.focus()
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(errorText(e, 'не удалось загрузить очередь'))
        setQueue([])
      })
    return () => {
      cancelled = true
    }
  }, [reload])

  useEffect(() => {
    if (current === null) return
    let cancelled = false
    api
      .submission(current)
      .then((v) => !cancelled && setView(v))
      .catch((e: unknown) => !cancelled && setError(errorText(e, 'не удалось открыть заявку')))
    return () => {
      cancelled = true
    }
  }, [current, reload])

  const open = (id: number) => {
    // Та же заявка: ключ эффекта не меняется, и сброшенный вид остался бы пустым навсегда.
    if (id === current) return
    setView(null)
    setCurrent(id)
    window.history.replaceState(null, '', `/moderation?id=${id}`)
  }

  const fields = view ? submissionFields(view.card, view.accepted) : []
  const split = OUTSIDE_KTRU_BLOCK ? splitOutsideKtru(fields) : { inCard: fields, outside: [] }
  const byField = view ? issuesByField(view.check.issues) : null
  const codes = view ? cardCodes(view.card) : { category: null, ktru: null }
  const category = usePosition(codes.category)
  const ktru = usePosition(codes.ktru)
  const clean = queue ? cleanPending(queue) : []
  const [acceptingClean, setAcceptingClean] = useState(false)
  const [accepted, setAccepted] = useState<number | null>(null)
  // Список, который модератор видит в диалоге: пачкой уходят ровно эти заявки (если всё ещё чистые).
  // Список живёт до конца анимации закрытия, иначе диалог успевает показать «Одобрить 0».
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirming, setConfirming] = useState<{ id: number; name: string }[]>([])

  const acceptClean = async (ids: number[]) => {
    setConfirmOpen(false)
    setAcceptingClean(true)
    setError(null)
    try {
      setAccepted((await api.acceptClean(ids)).accepted.length)
      // Кнопка «Одобрить чистые» исчезнет, если чистых не осталось: фокус — на итог, а не в никуда.
      focusAccepted.current = true
      setReload((n) => n + 1)
    } catch (e) {
      setError(errorText(e, 'не удалось одобрить заявки'))
    } finally {
      setAcceptingClean(false)
    }
  }

  return (
    <Page
      title="Заявки на модерацию"
      description="Очередь модератора портала. Модуль проверил каждую карточку и отметил нарушения прямо на полях."
    >
      <LlmSwitch />
      {error && (
        <Alert severity="error" className="mb-4">
          {error}
        </Alert>
      )}
      {accepted !== null && (
        <Alert ref={acceptedRef} tabIndex={-1} severity="success" className="mb-4" onClose={() => setAccepted(null)}>
          Одобрено в один клик: {accepted}. В журнале заявки записано, что модуль не нашёл замечаний.
        </Alert>
      )}
      {queue === null && <Skeleton variant="rounded" height={320} />}
      {queue && queue.length === 0 && (
        <Paper variant="outlined" className="p-8 text-center">
          <Typography variant="h6" component="h2">
            Заявок пока нет
          </Typography>
          <Typography color="text.secondary" className="mt-1">
            Поставщик отправляет карточку из формы «Создание СТЕ», и она появляется здесь.
          </Typography>
          <Button variant="contained" className="mt-4" onClick={() => go('/')}>
            Создать СТЕ
          </Button>
        </Paper>
      )}
      {queue && queue.length > 0 && (
        <Box className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <Paper variant="outlined" className="overflow-hidden lg:sticky lg:top-24">
            <Box className="flex flex-wrap items-center justify-between gap-2 px-4 pt-4 pb-2">
              <Typography variant="h6" component="h2">
                Очередь
              </Typography>
              {clean.length > 0 && (
                <Button
                  size="small"
                  variant="outlined"
                  disabled={acceptingClean}
                  onClick={() => {
                    setConfirming(clean)
                    setConfirmOpen(true)
                  }}
                >
                  {acceptingClean ? 'Одобряем…' : `Одобрить чистые (${clean.length})`}
                </Button>
              )}
            </Box>
            <List disablePadding aria-label="Очередь заявок">
              {queue.map((s) => (
                <ListItem key={s.id} disablePadding divider>
                  <ListItemButton
                    selected={s.id === current}
                    onClick={() => open(s.id)}
                    aria-current={s.id === current ? 'true' : undefined}
                    sx={{ gap: 1.5, alignItems: 'flex-start' }}
                  >
                    {/* Без фото — пометка на месте миниатюры: на портале изображение у товара обязательно. */}
                    <Box sx={{ mt: 0.75, flex: 'none' }}>
                      {s.card.photo_id != null ? <PhotoThumb photoId={s.card.photo_id} size={40} /> : <NoPhoto size={40} />}
                    </Box>
                    <ListItemText
                      sx={{ overflowWrap: 'anywhere' }}
                      primary={s.card.name}
                      secondary={
                        <>
                          <Box component="span" sx={{ display: 'block' }}>
                            №{NBSP}
                            {s.id}, {time.format(new Date(s.created_at))}
                          </Box>
                          <Box component="span" sx={{ display: 'block', mt: 0.5 }}>
                            <StatusMark tone={STATUS[s.status].tone}>
                              {s.status === 'pending' && s.decision_note ? 'исправлена' : STATUS[s.status].text}
                            </StatusMark>
                          </Box>
                          {/* Чистую одобряет кнопка «Одобрить чистые» над очередью. */}
                          {s.status === 'pending' && s.triage?.clean && (
                            <Box component="span" sx={{ display: 'block', color: 'success.dark' }}>
                              без замечаний модуля
                            </Box>
                          )}
                        </>
                      }
                    />
                  </ListItemButton>
                </ListItem>
              ))}
            </List>
          </Paper>

          <Paper variant="outlined" className="overflow-hidden">
            {!view || !byField ? (
              <Box className="p-6">
                <Skeleton variant="rounded" height={280} />
              </Box>
            ) : (
              <>
                <Box className="flex items-start gap-4 px-5 pt-5 pb-1 md:px-6">
                  <Box className="min-w-0 flex-1">
                    <Typography variant="overline" color="text.secondary">
                      Заявка №{NBSP}
                      {view.id}
                    </Typography>
                    <Typography variant="h5" component="h2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
                      {view.card.name}
                    </Typography>
                  </Box>
                  {view.card.photo_id != null ? <PhotoThumb key={view.card.photo_id} photoId={view.card.photo_id} size={112} /> : <NoPhoto size={112} />}
                </Box>
                <Box className="flex flex-col gap-4 px-5 py-4 md:px-6">
                  {byField.other.map((i) => (
                    <Alert
                      key={`${i.field}-${i.rule}`}
                      severity={i.severity === 'block' ? 'error' : i.severity === 'warn' ? 'warning' : 'info'}
                    >
                      {issueText(i)}
                    </Alert>
                  ))}
                  <NameField value={view.card.name} issue={byField.name[0]} readOnly />
                  {/* Категория портала (КПГЗ) и итоговая позиция КТРУ; у справочника из позиций КТРУ это один код. */}
                  {codes.category && (
                    <KtruField
                      value={codeWithName(codes.category, category?.name)}
                      helper={categoryPath(category?.path, category?.name) ?? undefined}
                      label="Категория портала (КПГЗ)"
                    />
                  )}
                  <KtruField value={codes.ktru ? codeWithName(codes.ktru, ktru?.name) : 'Не указана'} label="Позиция КТРУ" />
                  {fields.length > 0 ? (
                    <>
                      {split.inCard.length > 0 && <CharacteristicFields fields={split.inCard} issues={byField.fields} readOnly />}
                      <OutsideKtruBlock fields={split.outside} issues={byField.fields} />
                    </>
                  ) : (
                    <Alert severity="warning">В карточке нет характеристик.</Alert>
                  )}
                  {view.card.description && (
                    <Box className="p-4" sx={{ border: 1, borderColor: 'divider' }}>
                      <Typography variant="overline" color="text.secondary" component="h3">
                        Описание поставщика
                      </Typography>
                      <SourceText name={view.card.name} description={view.card.description} fields={fields} active={null} />
                    </Box>
                  )}
                </Box>
                <Box className="px-5 py-4 md:px-6" sx={{ borderTop: 1, borderColor: 'divider' }}>
                  <Decision key={`${view.id}-${view.status}`} view={view} onDone={() => setReload((n) => n + 1)} />
                </Box>
              </>
            )}
          </Paper>
        </Box>
      )}
      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        slotProps={{ transition: { onExited: () => setConfirming([]) } }}
        fullWidth
        maxWidth="sm"
        aria-labelledby="accept-clean-title"
      >
        <DialogTitle id="accept-clean-title" sx={{ fontSize: '1.125rem', fontWeight: 600 }}>
          Одобрить без просмотра: {confirming.length}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 1.5 }}>
            В этих заявках модуль не нашёл ни нарушений, ни предупреждений, в том числе по позиции КТРУ. В журнал каждой заявки
            запишется, что она одобрена в один клик.
          </DialogContentText>
          <List dense disablePadding aria-label="Заявки к одобрению" sx={{ maxHeight: 320, overflowY: 'auto', border: `1px solid ${TOKENS.line}` }}>
            {confirming.map((s) => (
              <ListItem key={s.id} divider>
                <ListItemText
                  sx={{ overflowWrap: 'anywhere', my: 0.25 }}
                  primary={s.name}
                  secondary={`№${NBSP}${s.id}`}
                />
              </ListItem>
            ))}
          </List>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button color="inherit" onClick={() => setConfirmOpen(false)} sx={{ color: TOKENS.ink2 }}>
            Отмена
          </Button>
          {/* Второй щелчок во время анимации закрытия не шлёт запрос повторно. */}
          <Button variant="contained" onClick={() => confirmOpen && acceptClean(confirming.map((s) => s.id))}>
            Одобрить {confirming.length}
          </Button>
        </DialogActions>
      </Dialog>
    </Page>
  )
}
