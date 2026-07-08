import { useState } from 'react'
import { apiClient } from './client'

export type JobPhase = 'idle' | 'running' | 'requires_confirmation' | 'failed' | 'completed'

interface PollResult<TResult> {
  phase: JobPhase
  stage: string | null
  error: string | null
  result: TResult | null
}

// Фронт поллит GET /jobs/{id} раз в 1с (FRONTEND.md §4) — общая логика для
// визарда дизайна (R5) и секции Анализ/Валидации (R6), чтобы не дублировать
// цикл поллинга в каждом месте.
export function useJobPolling<TResult = Record<string, unknown>>() {
  const [state, setState] = useState<PollResult<TResult>>({
    phase: 'idle', stage: null, error: null, result: null,
  })

  const poll = async (jobId: string): Promise<TResult | null> => {
    setState({ phase: 'running', stage: null, error: null, result: null })
    for (;;) {
      const { data } = await apiClient.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: jobId } } })
      if (!data) {
        setState({ phase: 'failed', stage: null, error: 'Не удалось получить статус задачи', result: null })
        return null
      }
      setState((prev) => ({ ...prev, stage: data.progress?.stage ?? null }))
      if (data.status === 'completed') {
        const result = (data.result ?? null) as TResult | null
        setState({ phase: 'completed', stage: null, error: null, result })
        return result
      }
      if (data.status === 'failed') {
        setState({ phase: 'failed', stage: null, error: data.error ?? 'Задача завершилась с ошибкой', result: null })
        return null
      }
      if (data.status === 'requires_confirmation') {
        setState({ phase: 'requires_confirmation', stage: null, error: null, result: data.result as TResult })
        return null
      }
      await new Promise((r) => setTimeout(r, 1000))
    }
  }

  const reset = () => setState({ phase: 'idle', stage: null, error: null, result: null })

  return { ...state, poll, reset }
}
