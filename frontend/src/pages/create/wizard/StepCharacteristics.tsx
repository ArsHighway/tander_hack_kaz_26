import { Alert, Box, Button, Link, Typography } from '@mui/material'
import { IconAlertCircle, IconChevronDown, IconCircleCheck } from '@tabler/icons-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { AutofillOut, Chain, Issue, Span, TwinOut } from '../../../api/v1.ts'
import { CharacteristicFields, NameField } from '../../../card/CardFields'
import { fieldId } from '../../../card/fieldId'
import { plural } from '../../../lib/format'
import { TOKENS } from '../../../theme'
import { fieldLabel } from '../createModel.ts'
import { issuesByField, pendingHints, type FieldState } from '../formModel.ts'
import { groupFields, readiness, visibleOptional } from './groups.ts'
import { PhotoField } from './PhotoField'
import { missingPhotoWarning } from './submit.ts'
import { TwinNotice } from './TwinNotice'

type Props = {
  name: string
  onName: (value: string) => void
  fields: FieldState[]
  issues: Issue[]
  /** Такие СТЕ уже есть на портале (`AutofillOut.twins`). */
  twins: TwinOut[]
  /** Куски описания, которые не легли ни в одно поле: видно, что из текста не потеряно. */
  unread?: Span[]
  /** Куда поставить непонятые куски (второй слой модели). */
  suggestions?: Suggestion[]
  /** Найденная категория и позиция КТРУ: почему выбрана именно эта. */
  chain?: Chain | null
  answeredAt: Record<string, 2 | 3>
  onChange: (name: string, value: string, unit?: string | null) => void
  onConfirm?: (name: string) => void
  /** «Принять все N» группы: подсказки похожих СТЕ и двойника этих полей — значениями. */
  onAcceptHints: (names: string[]) => void
  onFocusField: (name: string | null) => void
  onReview: () => void
  photoId: number | null
  photoUploading: boolean
  onPhotoUpload: (file: File) => Promise<void>
  onPhotoRemove: () => void
  /** Необязательные раскрыты: состояние у страницы — к свёрнутому полю ведут фрагменты «Ваш текст». */
  more: boolean
  onMore: (open: boolean) => void
  /** Уже 900 px: открыть живую карточку (шторку). */
  onOpenCard?: () => void
  /** «Отправить» на шаге 5 не отправило: что ещё мешает (`notSentText`); null — ничего или не отправляли. */
  notSent?: string | null
  /** До первой проверки пустые обязательные поля не красные: звёздочка и счётчик внизу. */
  quietRequired?: boolean
  busy: boolean
}

function Section({ title, description, action, children }: { title: string; description?: string; action?: ReactNode; children: ReactNode }) {
  return (
    // Раздел отделён линией сверху: разделы шага читаются блоками, а не сплошной лентой полей.
    <Box component="section" aria-label={title} className="flex flex-col gap-3 pt-6" sx={{ borderTop: `1px solid ${TOKENS.line}` }}>
      <Box className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        {/* Заголовок группы портала (h4 кита: Bold 20/24) крупнее подписей полей — SemiBold 16. */}
        <Typography component="h3" sx={{ fontSize: '1.25rem', lineHeight: '1.5rem', fontWeight: 700 }}>
          {title}
        </Typography>
        {action}
      </Box>
      {description && <Typography sx={{ color: TOKENS.mute, fontSize: '0.9375rem', mt: -0.5 }}>{description}</Typography>}
      {children}
    </Box>
  )
}

/** «Принять все N подсказок» группы: от двух непринятых подсказок, одну принимают у поля. */
function AcceptAll({ fields, group, onAccept }: { fields: FieldState[]; group: string; onAccept: (names: string[]) => void }) {
  const pending = pendingHints(fields)
  if (pending.length < 2) return null
  const n = pending.length
  const text = `Принять все ${n} ${plural(n, 'подсказку', 'подсказки', 'подсказок')}`
  // Имя для чтеца начинается с видимой подписи (WCAG 2.5.3), группа — уточнением.
  return (
    <Button size="small" variant="outlined" aria-label={`${text} в группе «${group}»`} onClick={() => onAccept(pending.map((f) => f.name))}>
      {text}
    </Button>
  )
}

const focus = (name: string) => document.getElementById(fieldId(name))?.focus()

/** «Осталось заполнить»: пустые обязательные характеристики ссылками на поля. */
export function Remaining({ left, className }: { left: string[]; className?: string }) {
  if (!left.length) return null
  return (
    <Typography className={className} sx={{ color: TOKENS.ink2, fontSize: '0.9375rem' }}>
      Осталось заполнить в КТРУ:{' '}
      {left.map((n, i) => (
        <span key={n}>
          {i > 0 && ', '}
          <Link component="button" type="button" onClick={() => focus(n)} sx={{ fontSize: '0.9375rem', verticalAlign: 'baseline', textAlign: 'left' }}>
            {n}
          </Link>
        </span>
      ))}
    </Typography>
  )
}

/** Шаг 4: поля карточки группами, у поля — откуда значение и замечание с исправлением. */
export function StepCharacteristics({ name, onName, fields, issues, twins, unread = [], suggestions = [], chain = null, onConfirm, answeredAt, onChange, onAcceptHints, onFocusField, onReview, photoId, photoUploading, onPhotoUpload, onPhotoRemove, more, onMore, onOpenCard, notSent, quietRequired, busy }: Props) {
  const byField = issuesByField(issues)
  const groups = groupFields(fields)
  const ready = readiness(fields)
  const common = { issues: byField.fields, onChange, onConfirm, onFocusField, answeredAt, quietRequired }
  const optional = visibleOptional(groups.portal, new Set(byField.fields.keys()), more)
  const optionalShown = groups.portal.filter((f) => optional.shown.includes(f.name))
  const hints = pendingHints(fields).length
  // Дошёл ли человек до конца формы: пока нет, над полосой отправки затухание и кнопка «Ниже ещё поля».
  const end = useRef<HTMLDivElement>(null)
  const [atEnd, setAtEnd] = useState(false)
  useEffect(() => {
    const el = end.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    // Низ экрана закрывает сама полоса: конец формы считается видным, только когда он выше неё.
    const io = new IntersectionObserver(([e]) => setAtEnd(e.isIntersecting), { rootMargin: '0px 0px -96px 0px' })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  const scrollOn = () => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    window.scrollBy({ top: Math.round(window.innerHeight * 0.6), behavior: reduce ? 'auto' : 'smooth' })
  }
  const notes = byField.other.filter((i) => !missingPhotoWarning(i) && i.rule !== MERGE_RULE)
  return (
    <Box className="flex flex-col gap-8">
      {/* Начало рабочей части: что это за шаг и что от человека нужно. */}
      <Box>
        <Typography component="h2" sx={{ fontSize: '1.5rem', lineHeight: '2rem', fontWeight: 700 }}>
          Характеристики товара
        </Typography>
        <Typography sx={{ mt: 0.5, color: TOKENS.ink2, maxWidth: '72ch' }}>
          Заполнили из вашего текста. Проверьте значения и допишите пустые; откуда взято значение, подскажет значок справа.
        </Typography>
      </Box>
      <KtruWhy chain={chain} />
      {/* Замечания модуля и подсказки — одной группой над формой, а не вперемешку с полями. */}
      {(notes.length > 0 || twins.length > 0 || unread.length > 0 || hints > 0) && (
        <Box className="flex flex-col gap-2">
          {notes.map((i) => (
            <Alert key={`${i.field}-${i.rule}`} severity={i.severity === 'block' ? 'error' : i.severity === 'warn' ? 'warning' : 'info'}>
              {fieldLabel(i.field)}: {i.message}
            </Alert>
          ))}
          <TwinNotice twins={twins} hinted={fields.some((f) => f.method === 'twin' && f.hint != null)} issues={issues} />
          <UnreadNotice pieces={unread} suggestions={suggestions} fields={fields} onPut={(f, v) => onChange(f, v)} />
          {/* Подсказки чужих СТЕ — предложение: в карточку только принятые. */}
          {hints > 0 && (
            <Alert severity="info">
              {`${plural(hints, 'Поле', 'Поля', 'Полей')} с подсказкой СТЕ портала: ${hints}. В карточку попадёт только то, что вы примете.`}
            </Alert>
          )}
        </Box>
      )}
      {/* На широком экране список стоит справа и не уезжает при прокрутке полей. */}
      <Remaining left={ready.left} className="min-[900px]:hidden" />
      {/* Как вкладка «Общие сведения» на странице СТЕ портала: наименование, фото, производитель, модель, страна. */}
      <Section title="Общие сведения" action={<AcceptAll fields={groups.general} group="Общие сведения" onAccept={onAcceptHints} />}>
        <NameField value={name} issue={byField.name[0]} onChange={onName} />
        <PhotoField photoId={photoId} uploading={photoUploading} onUpload={onPhotoUpload} onRemove={onPhotoRemove} />
        {groups.general.length > 0 && <CharacteristicFields fields={groups.general} {...common} />}
      </Section>
      {groups.ktru.length > 0 && (
        <Section title="Характеристики КТРУ" description="Из выбранной позиции КТРУ, звёздочкой отмечены обязательные">
          <CharacteristicFields fields={groups.ktru} {...common} />
        </Section>
      )}
      {groups.portal.length > 0 && (
        <Section title="Характеристики портала" description="Так их заполняют у похожих товаров на портале" action={<AcceptAll fields={optionalShown} group="Характеристики портала" onAccept={onAcceptHints} />}>
          {optionalShown.length > 0 && <CharacteristicFields fields={optionalShown} {...common} />}
          {(more || optional.hidden > 0) && (
            <Box>
              <Button variant="outlined" aria-expanded={more} onClick={() => onMore(!more)}>
                {more ? 'Свернуть' : `Показать ещё ${optional.hidden} ${plural(optional.hidden, 'характеристику', 'характеристики', 'характеристик')}`}
              </Button>
            </Box>
          )}
        </Section>
      )}
      {groups.new.length > 0 && (
        <Section title="Новые характеристики" description="Нашли в вашем тексте, но их нет ни в КТРУ, ни на портале. Их проверит модератор">
          <CharacteristicFields fields={groups.new} {...common} />
        </Section>
      )}
      <Box ref={end} aria-hidden sx={{ height: '1px', mt: -3 }} />
      {/* Полоса отправки — голубая с синей линией и тенью вверх: отдельный слой поверх формы, а не её строка.
          Пока форма не дочитана, над полосой затухание и «Ниже ещё поля». */}
      <Box className="sticky bottom-0 z-10 -mx-4 -mb-4 md:-mx-6 md:-mb-6">
        {!atEnd && (
          <Box
            aria-hidden
            sx={{ position: 'absolute', left: 0, right: 0, top: -48, height: 48, pointerEvents: 'none', background: 'linear-gradient(to bottom, rgba(255,255,255,0), #ffffff)' }}
          />
        )}
        <Box
          className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 md:px-6"
          sx={{ bgcolor: TOKENS.accSoft, borderTop: `2px solid ${TOKENS.acc}`, boxShadow: '0 -10px 30px rgba(38, 75, 130, 0.15)' }}
        >
        {/* В закреплённой полосе: видно и тогда, когда фокус увёл к полю внизу формы. */}
        {notSent && (
          <Typography role="status" aria-live="polite" className="w-full" sx={{ color: TOKENS.err, fontSize: '0.9375rem', fontWeight: 600 }}>
            {notSent}
          </Typography>
        )}
        <Box className="flex min-w-0 flex-1 items-center gap-2.5" role="status" aria-live="polite">
          {/* Значок полосы формы портала: зелёная галочка — обязательные заполнены, красный «!» — нет. */}
          {ready.done >= ready.total ? (
            <IconCircleCheck size={24} stroke={1.5} color="#0d9b68" aria-hidden />
          ) : (
            <IconAlertCircle size={24} stroke={1.5} color={TOKENS.err} aria-hidden />
          )}
          {/* Полоса заполненности — в карточке справа; здесь только счёт, чтобы полоса шага была в одну строку. */}
          {/* Короче, чем в карточке: полоса в одну строку и рядом с «Ниже ещё поля». */}
          <Typography sx={{ fontWeight: 600, fontSize: { xs: '0.9375rem', md: '1.0625rem' }, whiteSpace: 'nowrap' }}>
            Обязательные КТРУ: {ready.done} из {ready.total}
          </Typography>
        </Box>
        {onOpenCard && (
          <Button size="small" color="inherit" onClick={onOpenCard} sx={{ display: { md: 'none' }, color: TOKENS.ink2 }}>
            Карточка
          </Button>
        )}
        {!atEnd && (
          <Button onClick={scrollOn} startIcon={<IconChevronDown size={18} stroke={1.75} aria-hidden />} sx={{ display: { xs: 'none', lg: 'inline-flex' } }}>
            Ниже ещё поля
          </Button>
        )}
        <Box className="ml-auto shrink-0">
          {/* Пока фото загружается, к отправке не перейти: заявка ушла бы без него. */}
          <Button variant="contained" size="large" disabled={busy || photoUploading} onClick={onReview}>
            Проверить и отправить
          </Button>
        </Box>
        </Box>
      </Box>
    </Box>
  )
}

/** Что из описания не легло в поля: модуль ничего не выбрасывает молча. */
type Suggestion = NonNullable<AutofillOut['suggestions']>[number]

/** Не легло ни в одно поле — с подсказкой второй модели, куда это поставить (решает поставщик). */
function UnreadNotice({ pieces, suggestions, fields, onPut }: { pieces: Span[]; suggestions: Suggestion[]; fields: FieldState[]; onPut?: (field: string, value: string) => void }) {
  if (!pieces.length) return null
  const names = new Set(fields.map((f) => f.name))
  return (
    <Box component="section" aria-label="Не разобрано из описания" sx={{ border: `1px solid ${TOKENS.line}`, bgcolor: '#ffffff', p: 2 }}>
      <Typography sx={{ fontWeight: 600, fontSize: '0.9375rem', color: TOKENS.ink }}>
        {`Не разобрано из описания: ${pieces.length}`}
      </Typography>
      <Typography sx={{ fontSize: '0.875rem', color: TOKENS.ink2, mb: 1 }}>
        Этого нет ни в одном поле. Если это характеристика товара, перенесите её в нужное поле.
      </Typography>
      <Box component="ul" className="flex flex-col gap-2" sx={{ m: 0, pl: 2.5 }}>
        {pieces.map((s) => {
          const hint = suggestions.find((x) => x.quote === s.text)
          return (
            <Typography component="li" key={`${s.start}-${s.end}`} sx={{ fontSize: '0.9375rem', color: TOKENS.ink }}>
              {s.text}
              {hint && (
                <Box component="span" sx={{ display: 'block', fontSize: '0.875rem', color: TOKENS.ink2 }}>
                  {`Похоже на «${hint.field}»: ${hint.value}`}
                  {hint.in_list ? '' : hint.allowed.length ? ` — такого значения нет в перечне (${hint.allowed.slice(0, 6).join(', ')}${hint.allowed.length > 6 ? '…' : ''})` : ''}
                  {onPut && names.has(hint.field) && (
                    <Button size="small" variant="outlined" sx={{ ml: 1 }} onClick={() => onPut(hint.field, hint.value)}>
                      {`Поставить в «${hint.field}»`}
                    </Button>
                  )}
                </Box>
              )}
            </Typography>
          )
        })}
      </Box>
    </Box>
  )
}

const MERGE_RULE = 'одинаковые позиции КТРУ — берётся более свежая'

/** Почему в карточке эта позиция КТРУ: путь сужения и объединённые одинаковые позиции. Без этого
 * шаг 3 пролетает незаметно, и поставщик не знает, откуда код. */
export function KtruWhy({ chain }: { chain: Chain | null }) {
  const code = chain?.ktru?.final_code
  if (!chain || !code) return null
  const steps = (chain.ktru?.trail ?? []).map((s) => `${s.label}: ${s.remaining}`)
  return (
    <Box component="section" aria-label="Почему эта позиция КТРУ" sx={{ border: `1px solid ${TOKENS.line}`, bgcolor: '#fff', p: 2 }}>
      <Typography sx={{ fontWeight: 600, fontSize: '1rem', color: TOKENS.ink }}>
        {`Позиция КТРУ ${code}`}
      </Typography>
      {steps.length > 0 && (
        <Typography sx={{ fontSize: '0.875rem', color: TOKENS.ink2, mt: 0.5 }}>
          {`Как выбрана: ${steps.join(' → ')}`}
        </Typography>
      )}
      {/* Черновик вкладки и ответы старого API — без ktru_merged: страница не должна падать. */}
      {(chain.ktru_merged ?? []).map((m) => (
        <Typography key={m.kept} sx={{ fontSize: '0.875rem', color: TOKENS.ink, mt: 0.5 }}>
          {m.message} Если по документам нужна другая позиция, укажите её код в поле КТРУ.
        </Typography>
      ))}
    </Box>
  )
}
