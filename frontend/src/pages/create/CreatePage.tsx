import { IconRefresh, IconX } from '@tabler/icons-react'
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
  IconButton,
  Skeleton,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { ApiError, api, photoUrl, type AutofillOut, type Issue, type Position, type Submission } from '../../api/v1.ts'
import { fieldId } from '../../card/fieldId'
import { SourceText } from '../../card/SourceText'
import { NBSP } from '../../lib/format'
import { Page, TwoTone } from '../../shell/Page'
import { PAGE_TITLE_SX } from '../../shell/pageTitle.ts'
import { TOKENS } from '../../theme'
import { PREFILL_KEY, carriedAnswer, parsePrefill, type Prefill } from './createModel.ts'
import {
  acceptedFrom,
  acceptHints,
  fieldsOf,
  cardFrom,
  joinPasted,
  mergeFields,
  splitPasted,
  withOwn,
  type FieldState,
} from './formModel.ts'
import { CONFIRM, NOTHING_FITS } from './ktruView.ts'
import { readiness } from './wizard/groups.ts'
import { liveCard } from './wizard/liveCard.ts'
import { CardPlaceholder, CardSheet, LiveCard } from './wizard/LiveCardPanel'
import { portalPreview } from './wizard/portalPreview.ts'
import { PortalPreview } from './wizard/PortalPreviewPanel'
import { answerError, rememberAsked, type AskedSteps } from './wizard/history.ts'
import { categoryQuestion } from './wizard/options.ts'
import { pathSegments } from './wizard/path.ts'
import { Remaining, StepCharacteristics } from './wizard/StepCharacteristics'
import { NEXT_STEPS } from './wizard/nextSteps.ts'
import { StepDescribe } from './wizard/StepDescribe'
import { notFound, readyToReview, stepOf, type Step } from './wizard/stepOf.ts'
import { loadDraft, saveDraft } from './createDraft.ts'
import { CategoryDone, KtruDone, KtruNone, NotFound } from './wizard/StepPanels'
import { StepQuestion } from './wizard/StepQuestion'
import { Sent, StepSubmit } from './wizard/StepSubmit'
import { BLOCKED_ID, firstProblem, notSentText, submitState, type SubmitReport } from './wizard/submit.ts'
import { WizardPath } from './wizard/WizardPath'

/** Правка поля перепроверяется не на каждую букву, а после паузы. */
const RECHECK_MS = 500

/**
 * Карточка из «Массовой загрузки» или «Моих заявок». Чтение без побочных эффектов: в StrictMode начальное
 * состояние считается дважды, и удаление ключа здесь отдало бы второму вызову пустоту. Ключ удаляет эффект
 * после монтирования — повторный заход открывает пустую форму.
 */
function readPrefill(): Prefill | null {
  try {
    return parsePrefill(sessionStorage.getItem(PREFILL_KEY))
  } catch {
    return null
  }
}

function forgetPrefill(): void {
  try {
    sessionStorage.removeItem(PREFILL_KEY)
  } catch {
    // хранилище недоступно — читать нечего и удалять нечего
  }
}

/** Подпись под полем со значением самого поставщика. */
const ownLabel = (p: Prefill) => (p.revise ? 'Из вашей заявки' : 'Из файла массовой загрузки')

const errorText = (e: unknown) => (e instanceof ApiError ? e.detail : 'не удалось выполнить запрос')

/** Позиция справочника с перечнями значений; без неё поля остаются свободными, это не ошибка экрана. */
async function loadPosition(code: string | null, known: Position | null): Promise<Position | null> {
  if (!code) return null
  if (known?.code === code) return known
  return api.ktru(code).catch(() => null)
}

export function CreatePage() {
  // Характеристики поставщика и исправляемая заявка живут до «Начать заново».
  // Префилл из пакета или «Моих заявок» важнее черновика: человек пришёл с другим товаром.
  const [draft] = useState(() => (readPrefill() ? null : loadDraft()))
  const [carry, setCarry] = useState(() => readPrefill() ?? draft?.carry ?? null)
  const [text, setText] = useState(() => (carry && !draft ? joinPasted(carry.name, carry.description) : (draft?.text ?? '')))
  const [name, setName] = useState(draft?.name ?? '')
  const [description, setDescription] = useState(draft?.description ?? '')
  const [out, setOut] = useState<AutofillOut | null>(draft?.out ?? null)
  const [position, setPosition] = useState<Position | null>(draft?.position ?? null)
  const [fields, setFields] = useState<FieldState[]>(draft?.fields ?? [])
  const [issues, setIssues] = useState<Issue[]>(draft?.issues ?? [])
  const [edits, setEdits] = useState(draft?.edits ?? 0)
  const [active, setActive] = useState<string | null>(null)
  const [pending, setPending] = useState<'autofill' | 'answer' | 'submit' | null>(null)
  // Вопросы, на которые уже ответили значением из заявки или файла («этап:характеристика»).
  const carried = useRef(new Set<string>(draft?.carried ?? []))
  const carryAnswersDisabled = useRef(draft?.carryAnswersDisabled ?? false)
  const [error, setError] = useState<string | null>(null)
  // Действие, которое не удалось из-за ответа модуля: «Повторить» у ошибки запускает его снова.
  const [retry, setRetry] = useState<(() => void) | null>(null)
  const [expired, setExpired] = useState(false)
  const [submitted, setSubmitted] = useState<Submission | null>(draft?.submitted ?? null)
  // Пройденный шаг, который человек открыл в полосе шагов, и вопросы, на которые он уже ответил.
  const [visit, setVisit] = useState<Step | null>(draft?.visit ?? null)
  const [asked, setAsked] = useState<AskedSteps>(draft?.asked ?? {})
  // На каком шаге человек ответил о характеристике: подпись под полем «Ваш ответ на шаге 3».
  const [answeredAt, setAnsweredAt] = useState<Record<string, 2 | 3>>(draft?.answeredAt ?? {})
  // Шаг 5: сводка и итог проверки модерации (null — проверка идёт, 'failed' — модуль не ответил).
  const [reviewing, setReviewing] = useState(draft?.reviewing ?? false)
  const [checked, setChecked] = useState<Issue[] | null | 'failed'>(draft?.checked ?? null)
  // «Отправить» не отправило: у кнопки шага 4 строка, что ещё мешает, пока мешает.
  const [triedSend, setTriedSend] = useState(false)
  // Пустые обязательные поля краснеют только после первой проверки: до неё хватает звёздочки и счётчика.
  const [reviewedOnce, setReviewedOnce] = useState(false)
  // Необязательные характеристики шага 4 свёрнуты; фрагмент «Ваш текст» раскрывает их перед переходом к полю.
  const [moreOpen, setMoreOpen] = useState(false)
  // Префилл заполняется сразу; экран загрузки — только до этого первого заполнения, а не всякий раз без ответа.
  const [autostart, setAutostart] = useState(() => carry !== null && !draft)
  // Поколение мастера: «Начать заново» и новое заполнение делают ответы прежних запросов ненужными.
  const generation = useRef(0)
  // Фото товара: загружено на сервер сразу при выборе; у исправляемой заявки — её фото.
  const [photoId, setPhotoId] = useState<number | null>(() => draft?.photoId ?? carry?.photoId ?? null)
  const [photoUploading, setPhotoUploading] = useState(false)
  const photoToken = useRef(0)
  // Уже 900 px живая карточка открывается нижней шторкой; поле, к которому вести после её закрытия.
  const [sheet, setSheet] = useState(false)
  const focusAfterSheet = useRef<string | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [confirmRestart, setConfirmRestart] = useState(false)
  const narrow = useMediaQuery(useTheme().breakpoints.down('sm'))

  // Код категории (этап 1) и итоговый код КТРУ (этап 2) уходят в заявку и /check отдельно.
  const code = out?.ktru.final_code ?? null
  const ktruCode = out?.chain?.ktru?.final_code ?? null
  const card = useMemo(() => ({ ...cardFrom(name, description, fields, code, ktruCode), photo_id: photoId }), [name, description, fields, code, ktruCode, photoId])
  const auto = stepOf(out, { reviewing })
  const step: Step = visit ?? auto
  // Характеристика текущего вопроса: в живой карточке у неё «выберите слева».
  const askingAbout = auto === 2 ? out?.ktru.question?.characteristic : auto === 3 ? out?.chain?.ktru?.question?.characteristic : undefined
  const current = askingAbout && askingAbout !== CONFIRM ? askingAbout : null
  const view = useMemo(() => (out ? liveCard(out, fields, current, name, issues, answeredAt) : null), [out, fields, current, name, issues, answeredAt])
  const preview = useMemo(
    () =>
      portalPreview({
        name,
        fields,
        chain: out?.chain ?? null,
        photoUrl: photoId !== null ? photoUrl(photoId) : null,
        ktruCode: out?.chain?.ktru?.final_code ?? null,
      }),
    [name, fields, out, photoId],
  )
  const openPreview = () => {
    setSheet(false)
    setPreviewOpen(true)
  }

  /** Новый ответ модуля: свежая карточка (заполнение) или поверх правок человека (ответ на вопрос). */
  const apply = async (next: AutofillOut, fresh: boolean, g: number, selectionChanged = false): Promise<boolean> => {
    const pos = await loadPosition(next.chain?.ktru?.final_code ?? next.ktru.final_code, fresh ? null : position)
    if (g !== generation.current) return false
    setOut(next)
    setPosition(pos)
    if (selectionChanged) {
      setFields((prev) => mergeFields(prev, fieldsOf(next, pos), true))
      setEdits((n) => n + 1)
    } else if (fresh) {
      const own = carry?.characteristics ?? []
      const base = fieldsOf(next, pos)
      setFields(carry && own.length ? withOwn(base, own, pos, ownLabel(carry)) : base)
      // Замечания модуля посчитаны без значений поставщика: карточка с ними перепроверяется.
      if (own.length) setEdits((n) => n + 1)
    } else {
      setFields((prev) => mergeFields(prev, fieldsOf(next, pos)))
      setEdits((n) => n + 1)
    }
    setIssues(next.issues)
    setChecked(null)
    setTriedSend(false)
    setActive(null)
    return true
  }

  const fill = async () => {
    const parts = splitPasted(text)
    if (!parts.name) {
      setError('Вставьте хотя бы наименование товара: без него категорию не подобрать.')
      return
    }
    const g = ++generation.current
    carryAnswersDisabled.current = false
    setAutostart(false)
    setPending('autofill')
    setError(null)
    setExpired(false)
    setVisit(null)
    setAsked({})
    setAnsweredAt({})
    setReviewing(false)
    setMoreOpen(false)
    try {
      const next = await api.autofill(parts.name, parts.description)
      if (g !== generation.current) return
      setName(next.name || parts.name)
      setDescription(next.description || parts.description)
      await apply(next, true, g)
    } catch (e) {
      if (g !== generation.current) return
      setOut(null)
      setError(errorText(e))
    } finally {
      if (g === generation.current) setPending(null)
    }
  }

  const remember = (characteristic: string, value: string, at: 2 | 3) => {
    if (characteristic !== CONFIRM && value !== NOTHING_FITS) setAnsweredAt((a) => ({ ...a, [characteristic]: at }))
  }

  const answer = async (characteristic: string, value: string, stage: 'category' | 'ktru') => {
    if (!out) return
    const at = stage === 'category' ? 2 : 3
    const shown = stage === 'category' ? categoryQuestion(out.ktru) : out.chain?.ktru
    const g = ++generation.current
    setPending('answer')
    setError(null)
    setRetry(null)
    try {
      const next = await api.answer(out.session_id, characteristic, value, stage)
      if (g !== generation.current || !(await apply(next, false, g))) return
      setAsked((a) => rememberAsked(a, at, shown, value))
      remember(characteristic, value, at)
      setVisit(null)
      setReviewing(false)
    } catch (e) {
      if (g !== generation.current) return
      if (answerError(e) === 'expired') setExpired(true)
      else {
        setError(errorText(e))
        setRetry(() => () => void answer(characteristic, value, stage))
      }
    } finally {
      if (g === generation.current) setPending(null)
    }
  }

  /** Повторный выбор: новая сессия исключает устаревшие ответы дальнейшего сужения. */
  const rechoose = async (characteristic: string, value: string, stage: 'category' | 'ktru' = 'category') => {
    if (!out) return
    const at = stage === 'category' ? 2 : 3
    const before = asked[at]
    const shown = before?.state ?? (stage === 'category' ? categoryQuestion(out.ktru) : out.chain?.ktru)
    const g = ++generation.current
    carryAnswersDisabled.current = true
    setPending('answer')
    setError(null)
    setRetry(null)
    try {
      let next = await api.autofill(name, description)
      if (g !== generation.current) return
      // На шаге 3 сохраняем выбранный тип; все старые ответы внутри типа отбрасываются.
      if (stage === 'ktru' && out.ktru.final_code) {
        next = await api.answer(next.session_id, CONFIRM, out.ktru.final_code, 'category')
        if (g !== generation.current) return
      }
      const final = await api.answer(next.session_id, characteristic, value, stage)
      if (g !== generation.current || !(await apply(final, false, g, true))) return
      const kept: AskedSteps = stage === 'ktru' && asked[2] ? { 2: asked[2] } : {}
      setAsked(rememberAsked(kept, at, shown, value))
      setAnsweredAt((prev) => stage === 'ktru' ? Object.fromEntries(Object.entries(prev).filter(([, s]) => s === 2)) : {})
      remember(characteristic, value, at)
      setVisit(null)
      setReviewing(false)
      setMoreOpen(false)
    } catch (e) {
      if (g !== generation.current) return
      setError(errorText(e))
      setRetry(() => () => void rechoose(characteristic, value, stage))
    } finally {
      if (g === generation.current) setPending(null)
    }
  }

  // Строка пакета и возвращённая заявка заполняются сразу: человек нажал «Открыть в форме», а не «Вставить текст».
  const started = useRef(false)
  const fillRef = useRef(fill)
  useLayoutEffect(() => {
    fillRef.current = fill
  })
  useEffect(forgetPrefill, [])
  useEffect(() => {
    if (!carry || started.current) return
    started.current = true
    void fillRef.current()
  }, [carry])

  // Заявка или строка файла уже отвечали на вопрос мастера («Тип управления: Рычажный» в
  // возвращённой заявке): ответ уходит сам, один раз на вопрос — дальше решает человек.
  const answerRef = useRef(answer)
  useLayoutEffect(() => {
    answerRef.current = answer
  })
  useEffect(() => {
    if (!carry || !out || pending || carryAnswersDisabled.current) return
    const a = carriedAnswer(out, carry.characteristics, carried.current)
    if (!a) return
    carried.current.add(`${a.stage}:${a.characteristic}`)
    void answerRef.current(a.characteristic, a.value, a.stage)
  }, [carry, out, pending])

  // Правка человека: замечания пересчитываются проверкой модуля, а не угадываются на экране.
  const cardRef = useRef(card)
  useLayoutEffect(() => {
    cardRef.current = card
  })
  useEffect(() => {
    if (edits === 0 || pending || !out?.ktru.final_code || submitted) return
    let cancelled = false
    const timer = setTimeout(() => {
      const g = generation.current
      api
        .check(cardRef.current)
        .then((r) => !cancelled && g === generation.current && setIssues(r.issues))
        .catch(() => undefined)
    }, RECHECK_MS)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [edits, pending, out, submitted])

  const edit = (field: string, value: string, unit?: string | null) => {
    setFields((prev) => prev.map((f) => (f.name === field ? { ...f, value, edited: true, ...(unit !== undefined ? { unit, unitName: unit === f.unit ? f.unitName : null, compact: null } : {}) } : f)))
    setEdits((n) => n + 1)
  }
  /** «Принять все N» группы: подсказки становятся значениями, карточка перепроверяется. */
  const acceptAll = (names: string[]) => {
    setFields((prev) => acceptHints(prev, names))
    setEdits((n) => n + 1)
  }
  const editName = (value: string) => {
    setName(value)
    setEdits((n) => n + 1)
  }

  /** «Проверить и отправить»: шаг 5 и проверка модуля по правилам модерации. */
  const review = () => {
    const g = generation.current
    setReviewing(true)
    setReviewedOnce(true)
    setVisit(null)
    setChecked(null)
    setError(null)
    setTriedSend(false)
    api
      .check(card)
      .then((r) => {
        if (g !== generation.current) return
        setChecked(r.issues)
        setIssues(r.issues)
      })
      // Модуль не ответил — это не «правила пройдены»: шаг 5 предложит повторить проверку.
      .catch(() => g === generation.current && setChecked('failed'))
  }

  // Модуль всё решил сам и заполнил из текста: сразу проверка (шаг 5) — один раз на заполнение,
  // к шагу 4 человек вернётся по полосе шагов. С подсказками похожих СТЕ открывается шаг 4.
  const autoReviewed = useRef(-1)
  const latestReview = useRef(review)
  useLayoutEffect(() => {
    latestReview.current = review
  })
  // Исправление заявки и строка пакета (carry) — человек пришёл смотреть поля: шаг 4 без автоперехода.
  const autoReady = !carry && !pending && !reviewing && visit === null && readyToReview(out, fields, issues)
  useEffect(() => {
    if (!autoReady || autoReviewed.current === generation.current) return
    // После рендера: проверяется карточка, которую видит человек.
    const t = setTimeout(() => {
      if (autoReviewed.current === generation.current) return
      autoReviewed.current = generation.current
      latestReview.current()
    }, 0)
    return () => clearTimeout(t)
  }, [autoReady])

  const submit = async () => {
    const g = generation.current
    setPending('submit')
    setError(null)
    setRetry(null)
    try {
      const check = await api.check(card)
      if (g !== generation.current) return
      // Сразу на экран: если отправить нельзя, фокус ставится на уже нарисованную плашку или поле.
      flushSync(() => {
        setIssues(check.issues)
        setChecked(check.issues)
      })
      const report = submitState(check.issues, fields)
      if (report.state === 'blocked') return leadTo(report)
      const s = { card, accepted: acceptedFrom(fields), batch: carry?.batch ?? null }
      const done = await (carry?.revise ? api.resubmit(carry.revise.id, s) : api.submit(s))
      if (g === generation.current) setSubmitted(done)
    } catch (e) {
      if (g !== generation.current) return
      setError(errorText(e))
      setRetry(() => () => void submit())
    } finally {
      if (g === generation.current) setPending(null)
    }
  }

  /** Фото уходит на сервер сразу; ответ после «Начать заново» — к прежней карточке, не к новой. */
  const uploadPhoto = async (file: File) => {
    // Своя метка, а не поколение мастера: фото относится к товару, и другая категория или
    // «Изменить описание» его не отменяют — только «Начать заново» (другой товар).
    const t = photoToken.current
    setPhotoUploading(true)
    try {
      const { photo_id } = await api.uploadPhoto(file)
      if (t === photoToken.current) setPhotoId(photo_id)
    } finally {
      if (t === photoToken.current) setPhotoUploading(false)
    }
  }

  // Черновик — после каждой правки состояния: ушёл со страницы и вернулся — карточка на месте.
  useEffect(() => {
    saveDraft({
      text, name, description, carry, carried: [...carried.current], carryAnswersDisabled: carryAnswersDisabled.current, out, position, fields, issues, edits,
      visit, asked, answeredAt, reviewing, checked, photoId, submitted,
    })
  }, [text, name, description, carry, out, position, fields, issues, edits, visit, asked, answeredAt, reviewing, checked, photoId, submitted])

  const reset = (keepCarry: boolean) => {
    generation.current += 1
    carried.current = new Set()
    carryAnswersDisabled.current = false
    setRetry(null)
    setPending(null)
    setMoreOpen(false)
    if (!keepCarry) {
      setCarry(null)
      photoToken.current += 1
      setPhotoUploading(false)
      setPhotoId(null)
    }
    setOut(null)
    setFields([])
    setIssues([])
    setSubmitted(null)
    setError(null)
    setExpired(false)
    setEdits(0)
    setVisit(null)
    setAsked({})
    setAnsweredAt({})
    setReviewing(false)
    setChecked(null)
    setTriedSend(false)
    setReviewedOnce(false)
  }
  const restart = () => {
    reset(false)
    setText('')
  }
  /** «Изменить описание»: шаг 1 с тем же текстом. */
  const editText = () => {
    setText(joinPasted(name, description))
    reset(true)
  }
  const pick = (s: Step) => {
    if (s === 4) {
      setReviewing(false)
      setVisit(null)
    } else setVisit(s)
  }

  const chain = out?.chain ?? null
  const ready = out ? readiness(fields) : null
  const segments = pathSegments(out, step, pending === 'autofill' || pending === 'answer' ? pending : null, ready)
  const pickable: Step[] = [
    ...(out?.ktru.final_code ? [2 as Step] : []),
    // Шаг 3 открывается и после «Ни одна не подходит»: позиции КТРУ подбирались, ответ можно изменить.
    ...(chain && (chain.ktru?.final_code || chain.ktru_missing || (chain.ktru && !chain.ktru.question)) ? [3 as Step] : []),
    ...(reviewing && !submitted ? [4 as Step] : []),
  ]
  const title = out ? name : carry?.revise ? `Исправление заявки №${NBSP}${carry.revise.id}` : 'Новая СТЕ'
  // Над заголовком — только «Исправление заявки №…»: у новой карточки подпись «Новая СТЕ» ничего не добавляла.
  const crumb = out && carry?.revise ? `Исправление заявки №${NBSP}${carry.revise.id}` : null
  const busy = pending !== null

  const main = () => {
    if (pending === 'autofill' || (!out && autostart && !error)) {
      return (
        <Box className="flex flex-col gap-3" aria-busy="true">
          <Typography sx={{ color: TOKENS.ink2 }}>Ищем категорию и заполняем характеристики…</Typography>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} variant="rounded" height={56} />
          ))}
        </Box>
      )
    }
    if (!out) return <StepDescribe text={text} onText={setText} onFill={fill} error={error} busy={busy} />
    if (submitted) return <Sent submission={submitted} revise={Boolean(carry?.revise)} onAgain={restart} />
    if (step === 2) {
      if (visit === 2 && asked[2]) {
        const prev = asked[2]
        return (
          <StepQuestion
            key="revisit-2"
            state={prev.state}
            stage="category"
            busy={busy}
            chosen={prev.chosen}
            // Тот же вариант — просто вернуться: новая сессия стёрла бы ответы шага 3 и правки шага 4.
            onAnswer={(c, v) => (v === prev.chosen ? setVisit(null) : rechoose(c, v))}
          />
        )
      }
      if (visit === 2 && out.ktru.final_code) {
        return (
          <CategoryDone
            out={out}
            busy={busy}
            current={auto}
            onNothing={() => rechoose(CONFIRM, NOTHING_FITS)}
            onReturn={() => setVisit(null)}
          />
        )
      }
      if (out.ktru.question) {
        return (
          <StepQuestion
            key={`${out.session_id}-2-${out.ktru.question.characteristic}`}
            state={categoryQuestion(out.ktru)}
            stage="category"
            busy={busy}
            onAnswer={(c, v) => answer(c, v, 'category')}
          />
        )
      }
      if (notFound(out)) return <NotFound steps={out.ktru.steps} onEdit={editText} />
    }
    if (step === 3 && chain) {
      if (visit === 3 && asked[3]) {
        const prev = asked[3]
        return (
          <StepQuestion
            key="revisit-3"
            state={prev.state}
            stage="ktru"
            busy={busy}
            chosen={prev.chosen}
            onAnswer={(c, v) => (v === prev.chosen ? setVisit(null) : rechoose(c, v, 'ktru'))}
            onBack={() => setVisit(2)}
          />
        )
      }
      if (visit === 3 && chain.ktru && !chain.ktru.final_code) return <KtruNone steps={chain.ktru.steps} busy={busy} onBack={() => setVisit(2)} />
      if (visit === 3) return <KtruDone out={out} current={auto} onReturn={() => setVisit(null)} />
      if (chain.ktru?.question) {
        return (
          <StepQuestion
            key={`${out.session_id}-3-${chain.ktru.question.characteristic}`}
            state={chain.ktru}
            stage="ktru"
            busy={busy}
            onAnswer={(c, v) => answer(c, v, 'ktru')}
            onBack={() => setVisit(2)}
          />
        )
      }
      if (chain.ktru && !chain.ktru.final_code) return <KtruNone steps={chain.ktru.steps} busy={busy} onBack={() => setVisit(2)} />
    }
    if (step === 5) {
      return (
        <StepSubmit
          out={out}
          name={name}
          fields={fields}
          issues={checked}
          onRetry={review}
          preview={preview}
          photoId={photoId}
          photoUploading={photoUploading}
          busy={busy}
          revise={Boolean(carry?.revise)}
          onSubmit={submit}
          onBlocked={leadTo}
          onGoToField={openField}
          onBack={() => pick(4)}
        />
      )
    }
    return (
      <StepCharacteristics
        name={name}
        onName={editName}
        fields={fields}
        issues={issues}
        twins={out?.twins ?? []}
        unread={out?.unread ?? []}
        suggestions={out?.suggestions ?? []}
        chain={out?.chain ?? null}
        answeredAt={answeredAt}
        onChange={edit}
        onConfirm={(field) => setFields((prev) => prev.map((f) => (f.name === field ? { ...f, confirmed: true } : f)))}
        onAcceptHints={acceptAll}
        onFocusField={setActive}
        onReview={review}
        photoId={photoId}
        photoUploading={photoUploading}
        onPhotoUpload={uploadPhoto}
        onPhotoRemove={() => setPhotoId(null)}
        more={moreOpen}
        onMore={setMoreOpen}
        onOpenCard={() => setSheet(true)}
        notSent={triedSend ? notSentText(issues, fields) : null}
        quietRequired={!reviewedOnce && !triedSend}
        busy={busy}
      />
    )
  }

  const goToField = (f: string) => {
    const target = fields.find((x) => x.name === f)
    // Необязательные свёрнуты: поле надо отрисовать до фокуса, иначе фокусировать нечего.
    if (target && !target.required && target.group !== 'general' && !moreOpen) flushSync(() => setMoreOpen(true))
    document.getElementById(fieldId(f))?.focus()
  }
  /** С шага 5 к полю: поля есть только на шаге 4, его надо отрисовать до фокуса. */
  const openField = (f: string) => {
    flushSync(() => {
      setReviewing(false)
      setVisit(null)
    })
    goToField(f)
  }
  /** «Отправить», когда отправить нельзя: к первому полю, которое мешает; причина — строкой у кнопки шага 4.
   *  Мешает не поле (наименование, код) — фокус на красной плашке шага 5. */
  function leadTo(report: SubmitReport) {
    const f = firstProblem(report, fields)
    if (f === null) {
      document.getElementById(BLOCKED_ID)?.focus()
      return
    }
    setTriedSend(true)
    openField(f)
  }
  // Поля есть только на шаге 4: на других шагах фрагмент текста никуда не ведёт и кнопкой не притворяется.
  const fieldsShown = step === 4 && !submitted
  /** «Ваш текст»: фрагменты ведут к полям; из шторки — после того, как она закроется. */
  const source = (inSheet: boolean): ReactNode => (
    <Box>
      <SourceText
        name={name}
        description={description}
        fields={fields}
        unread={step === 4 ? out?.unread ?? [] : []}
        active={active}
        onPick={
          fieldsShown
            ? (f) => {
                if (!inSheet) return goToField(f)
                focusAfterSheet.current = f
                setSheet(false)
              }
            : undefined
        }
      />
      <Typography sx={{ mt: 1.5, fontSize: '0.8125rem', color: TOKENS.mute }}>{fieldsShown ? 'Цветом выделено, как модуль прочитал текст. Нажмите на зелёный или оранжевый фрагмент, чтобы перейти к полю.' : 'Подчёркнуто: взято из вашего текста.'}</Typography>
      {!submitted && (
        <Button
          size="small"
          className="mt-1"
          disabled={busy}
          onClick={() => {
            setSheet(false)
            editText()
          }}
        >
          Изменить описание
        </Button>
      )}
    </Box>
  )
  const left = ready?.left ?? []
  const remaining =
    step === 4 && !submitted && left.length > 0 ? (
      <Box className="pb-3" sx={{ borderBottom: `1px solid ${TOKENS.line}` }}>
        <Remaining left={left} />
      </Box>
    ) : undefined
  // Полоса шторки внизу экрана: на шаге 4 её заменяет кнопка «Карточка» в полосе шага.
  const sheetBar = Boolean(view) && !submitted && step !== 4

  const alerts = (
    <>
      {carry?.revise && out && !submitted && (
        <Alert severity="warning">
          <AlertTitle sx={{ fontWeight: 600 }}>Замечания модератора</AlertTitle>
          <span className="whitespace-pre-line">{carry.revise.note || 'Модератор вернул заявку без текста.'}</span>
        </Alert>
      )}
      {expired && (
        <Alert
          severity="warning"
          action={
            <Button color="inherit" size="small" onClick={fill}>
              Заполнить заново
            </Button>
          }
        >
          Сессия заполнения истекла. Текст сохранён, его можно заполнить заново.
        </Alert>
      )}
      {error && out && (
        <Alert
          severity="error"
          action={
            retry ? (
              <Button
                color="inherit"
                size="small"
                disabled={busy}
                onClick={() => {
                  setRetry(null)
                  retry()
                }}
              >
                Повторить
              </Button>
            ) : undefined
          }
        >
          {error}
        </Alert>
      )}
    </>
  )

  return (
    <Page>
      <Box className="flex flex-col gap-4" sx={{ pb: { xs: sheetBar ? 8 : 0, md: 0 } }}>
        {/* Сверху — что за товар и где мы: заголовок и полоса шагов во всю ширину. */}
        <Box className="flex flex-col gap-4 p-4 md:px-6 md:pt-5 md:pb-5" sx={{ bgcolor: 'background.paper' }}>
          <Box className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
            <Box className="min-w-0 flex-1">
              {crumb && <Typography sx={{ fontSize: '0.875rem', color: TOKENS.mute }}>{crumb}</Typography>}
              {/* Наименование поставщика бывает на 200 знаков: длинное — мельче, чтобы шаги остались на экране. */}
              <Typography
                component="h1"
                // Как заголовки других экранов («Массовая загрузка»); очень длинное наименование — мельче.
                sx={title.length > 80 ? { ...PAGE_TITLE_SX, fontSize: { xs: '1.125rem', md: '1.375rem' } } : PAGE_TITLE_SX}
              >
                {out ? title : <TwoTone text={title} />}
              </Typography>
            </Box>
            {/* Кнопкой с обводкой у заголовка: видно, где начать другой товар. Отправленную карточку терять нечего;
                незаконченную — текст, ответы и правки, поэтому сначала вопрос. */}
            {out && (
              <Button
                variant="outlined"
                startIcon={<IconRefresh size={18} stroke={1.75} aria-hidden />}
                onClick={() => (submitted ? restart() : setConfirmRestart(true))}
                sx={{ flex: 'none' }}
              >
                Начать заново
              </Button>
            )}
          </Box>
          <WizardPath segments={segments} pickable={pickable} onPick={pick} />
        </Box>
        {/* Экран на две части: слева работа текущего шага, справа карточка товара отдельной панелью. */}
        <Box className="grid grid-cols-1 items-start gap-4 min-[900px]:grid-cols-[minmax(0,1fr)_22.5rem] min-[1200px]:grid-cols-[minmax(0,1fr)_27.5rem] min-[1800px]:grid-cols-[minmax(0,1fr)_30rem]">
          <Box className="flex min-w-0 flex-col gap-4 p-4 md:p-6" sx={{ bgcolor: 'background.paper' }}>
            {alerts}
            {main()}
          </Box>
          <Box
            component="aside"
            aria-label="Живая карточка"
            className="hidden p-5 min-[900px]:sticky min-[900px]:top-[124px] min-[900px]:block min-[1024px]:top-24"
            sx={{
              bgcolor: 'background.paper',
              maxHeight: 'calc(100dvh - 8.5rem)',
              overflowY: 'auto',
              scrollbarWidth: 'thin',
              scrollbarColor: `${TOKENS.line} transparent`,
            }}
          >
            {out && view ? (
              <LiveCard view={view} pulse={out} source={source(false)} top={remaining} onPreview={openPreview} photoId={photoId} showTrail={step >= 4} />
            ) : (
              <CardPlaceholder steps={NEXT_STEPS} />
            )}
          </Box>
        </Box>
        {out && view && !submitted && (
          <CardSheet
            view={view}
            open={sheet}
            bar={sheetBar}
            onOpen={() => setSheet(true)}
            onClose={() => setSheet(false)}
            onExited={() => {
              const f = focusAfterSheet.current
              focusAfterSheet.current = null
              if (f) goToField(f)
            }}
          >
            <LiveCard view={view} pulse={out} source={source(true)} sheet onPreview={openPreview} />
          </CardSheet>
        )}
      </Box>
      <Dialog open={confirmRestart} onClose={() => setConfirmRestart(false)} aria-labelledby="restart-title">
        <DialogTitle id="restart-title" sx={{ fontSize: '1.125rem', fontWeight: 600 }}>
          Начать заново?
        </DialogTitle>
        <DialogContent>
          <DialogContentText>Текст, ответы и правки этой карточки не сохранятся.</DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button color="inherit" onClick={() => setConfirmRestart(false)} sx={{ color: TOKENS.ink2 }}>
            Продолжить заполнение
          </Button>
          <Button
            variant="contained"
            onClick={() => {
              setConfirmRestart(false)
              restart()
            }}
          >
            Начать заново
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog open={previewOpen} onClose={() => setPreviewOpen(false)} fullWidth maxWidth="md" fullScreen={narrow} aria-labelledby="portal-preview-title">
        <DialogTitle id="portal-preview-title" sx={{ fontSize: '1.125rem', fontWeight: 600, pr: 7 }}>
          Как на портале
        </DialogTitle>
        <IconButton aria-label="Закрыть" onClick={() => setPreviewOpen(false)} sx={{ position: 'absolute', right: 12, top: 12, color: TOKENS.ink2 }}>
          <IconX size={20} stroke={1.5} aria-hidden />
        </IconButton>
        <DialogContent sx={{ pt: 0, px: { xs: 1.5, sm: 3 } }}>
          <PortalPreview view={preview} />
        </DialogContent>
      </Dialog>
    </Page>
  )
}
