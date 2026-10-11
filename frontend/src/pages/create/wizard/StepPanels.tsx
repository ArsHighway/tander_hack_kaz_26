import { Box, Button, Typography } from '@mui/material'
import type { ReactNode } from 'react'
import type { AutofillOut } from '../../../api/v1.ts'
import { MONO, TOKENS } from '../../../theme'
import { ktruText } from '../chainView.ts'
import { categoryNote, twinNote } from './options.ts'
import { KtruTrail } from './KtruTrail'
import { trailOf } from './trail.ts'

function Panel({ title, children, actions }: { title: string; children: ReactNode; actions: ReactNode }) {
  return (
    <Box className="flex flex-col gap-4">
      <Typography component="h2" sx={{ fontSize: '1.25rem', fontWeight: 600, lineHeight: 1.3 }}>
        {title}
      </Typography>
      {children}
      <Box className="flex flex-wrap items-center gap-2.5">{actions}</Box>
    </Box>
  )
}

function Result({ name, code, note }: { name: string; code: string | null; note: string | null }) {
  return (
    <Box sx={{ border: `1px solid ${TOKENS.line}`, borderRadius: 0, px: 1.75, py: 1.5 }}>
      <Typography sx={{ fontWeight: 600, fontSize: '1rem', overflowWrap: 'anywhere' }}>{name}</Typography>
      {code && <Typography sx={{ fontFamily: MONO, fontSize: '0.8125rem', color: TOKENS.ink2 }}>{code}</Typography>}
      {note && <Typography sx={{ mt: 0.5, fontSize: '0.875rem', color: TOKENS.ink2 }}>{note}</Typography>}
    </Box>
  )
}

const back = (step: number) => `Вернуться к шагу ${step}`

/** Шаг 2 после выбора: какая категория и почему; «Ни одна не подходит» — честный отказ. */
export function CategoryDone({ out, busy, current, onNothing, onReturn }: {
  out: AutofillOut
  busy: boolean
  current: number
  onNothing: () => void
  onReturn: () => void
}) {
  const final = out.ktru.candidates.find((c) => c.code === out.ktru.final_code)
  const name = out.chain?.category_name ?? final?.name ?? out.ktru.final_code ?? ''
  const code = out.chain?.kpgz ?? out.ktru.final_code
  const note = twinNote(out.twins) ?? (final ? categoryNote(final) : null)
  return (
    <Panel
      title="Тип товара"
      actions={
        <>
          <Button variant="contained" onClick={onReturn}>
            {back(current)}
          </Button>
          <Button variant="outlined" disabled={busy} onClick={onNothing}>
            Ни одна не подходит
          </Button>
        </>
      }
    >
      <Result name={name} code={code} note={note} />
      <KtruTrail trail={trailOf(out)} />
      {(out.chain?.path.length ?? 0) > 0 && (
        <Typography sx={{ fontSize: '0.875rem', color: TOKENS.mute, overflowWrap: 'anywhere' }}>{out.chain?.path.join(' › ')}</Typography>
      )}
    </Panel>
  )
}

/** Шаг 3 без вопроса: позиция КТРУ определена, её нет в КТРУ или нет в справочнике. */
export function KtruDone({ out, current, onReturn }: { out: AutofillOut; current: number; onReturn: () => void }) {
  const chain = out.chain
  if (!chain) return null
  const view = ktruText(chain)
  const final = chain.ktru?.candidates.find((c) => c.code === chain.ktru?.final_code)
  return (
    <Panel
      title="Позиция КТРУ"
      actions={
        <Button variant="contained" onClick={onReturn}>
          {back(current)}
        </Button>
      }
    >
      {final ? (
        <Result name={final.name} code={final.code} note="Определена по характеристикам товара" />
      ) : (
        <Result name={view.value} code={null} note={view.helper} />
      )}
    </Panel>
  )
}

/** Шаг 2: категория не найдена — что сделать с описанием. */
export function NotFound({ steps, onEdit }: { steps: string[]; onEdit: () => void }) {
  return (
    <Panel
      title="Категория не найдена"
      actions={
        <Button variant="contained" onClick={onEdit}>
          Изменить описание
        </Button>
      }
    >
      <Box component="ul" sx={{ m: 0, pl: 2.5, color: TOKENS.ink2, display: 'grid', gap: 0.75 }}>
        {(steps.length ? steps : ['В справочнике нет подходящей категории: уточните наименование товара.']).map((s) => (
          <li key={s}>{s}</li>
        ))}
      </Box>
    </Panel>
  )
}

/** Шаг 3: ни одна позиция КТРУ категории не подошла к ответам. */
export function KtruNone({ steps, busy, onBack }: { steps: string[]; busy: boolean; onBack: () => void }) {
  return (
    <Panel
      title="Не подходит ни одна позиция КТРУ"
      actions={
        <Button variant="contained" disabled={busy} onClick={onBack}>
          Вернуться к категории
        </Button>
      }
    >
      <Box component="ul" sx={{ m: 0, pl: 2.5, color: TOKENS.ink2, display: 'grid', gap: 0.75 }}>
        {(steps.length ? steps : ['Проверьте ответы на шаге 3 или выберите другую категорию.']).map((s) => (
          <li key={s}>{s}</li>
        ))}
      </Box>
    </Panel>
  )
}
