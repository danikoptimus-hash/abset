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

  // A single failed poll can just be a transient blip (or the backend
  // container restarting after a crash, e.g. an OOM-killed worker) —
  // giving up immediately used to show a generic "Failed to get job
  // status" even though the backend recovers a few seconds later with a
  // real job.error (mark_unfinished_jobs_failed_on_startup / the job
  // heartbeat timeout, see backend/jobs/runner.py). Retry a bounded number
  // of times first; only give up (and stop polling for good) if the
  // worker really seems gone.
  const MAX_CONSECUTIVE_FAILURES = 5

  const poll = async (jobId: string): Promise<TResult | null> => {
    setState({ phase: 'running', stage: null, error: null, result: null })
    let consecutiveFailures = 0
    for (;;) {
      const { data } = await apiClient.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: jobId } } })
      if (!data) {
        consecutiveFailures += 1
        if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
          setState({
            phase: 'failed',
            stage: null,
            error: 'Analysis worker stopped unexpectedly. Check server logs or try again.',
            result: null,
          })
          return null
        }
        await new Promise((r) => setTimeout(r, 1000))
        continue
      }
      consecutiveFailures = 0
      setState((prev) => ({ ...prev, stage: data.progress?.stage ?? null }))
      if (data.status === 'completed') {
        const result = (data.result ?? null) as TResult | null
        setState({ phase: 'completed', stage: null, error: null, result })
        return result
      }
      if (data.status === 'failed') {
        setState({ phase: 'failed', stage: null, error: data.error ?? 'Job failed', result: null })
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
