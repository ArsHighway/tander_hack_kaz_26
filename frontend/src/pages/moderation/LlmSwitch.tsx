import { Alert, FormControlLabel, Paper, Skeleton, Switch, Typography } from '@mui/material'
import { useEffect, useState } from 'react'
import { ApiError, api } from '../../api/v1.ts'
import { llmHint, llmLabel } from './moderationModel.ts'

type State = { kind: 'loading' } | { kind: 'ready'; enabled: boolean } | { kind: 'unreadable' }

const HINT_ID = 'llm-switch-hint'

/**
 * Выключатель модели (LLM) в шапке «Модерации»: общая настройка модуля, её читают API и воркеры пакетов.
 * Поставщик его не видит — блок есть только на этой странице. Состояние берётся с сервера после каждого
 * переключения, а не из локального значения: так видно, что настройка действительно сохранена.
 */
export function LlmSwitch() {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .settings()
      .then((s) => !cancelled && setState({ kind: 'ready', enabled: s.llm_enabled }))
      .catch(() => !cancelled && setState({ kind: 'unreadable' }))
    return () => {
      cancelled = true
    }
  }, [])

  const toggle = async (enabled: boolean) => {
    setBusy(true)
    setError(null)
    try {
      const saved = await api.setLlmEnabled(enabled)
      setState({ kind: 'ready', enabled: saved.llm_enabled })
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : 'Не удалось сохранить настройку модели')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Paper variant="outlined" className="mb-4 px-4 py-3" component="section" aria-label="Настройка модели">
      {state.kind === 'loading' && <Skeleton variant="rounded" height={40} />}
      {state.kind === 'unreadable' && <Alert severity="warning">Не удалось прочитать настройку модели: сервис не отвечает.</Alert>}
      {state.kind === 'ready' && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
          <FormControlLabel
            sx={{ m: 0 }}
            control={
              <Switch
                checked={state.enabled}
                disabled={busy}
                onChange={(_, checked) => toggle(checked)}
                slotProps={{ input: { 'aria-describedby': HINT_ID } }}
              />
            }
            label={<span style={{ fontWeight: 600 }}>{llmLabel(state.enabled)}</span>}
          />
          <Typography id={HINT_ID} sx={{ fontSize: '0.9375rem', color: 'text.secondary', flex: '1 1 320px', maxWidth: '80ch' }}>
            {llmHint()}
          </Typography>
        </div>
      )}
      {error && (
        <Alert severity="error" className="mt-2" role="alert">
          {error}
        </Alert>
      )}
    </Paper>
  )
}
