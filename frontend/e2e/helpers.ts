import type { APIRequestContext, Page } from '@playwright/test'
import { expect } from '@playwright/test'

// Локально: uvicorn напрямую (localhost:8000). В CI (e2e-джоба против
// реального docker compose стека) — через внешний nginx на E2E_BASE_URL
// (например http://localhost:8080/api/v1), см. .github/workflows/ci.yml.
const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

export async function loginViaUi(page: Page, email = 'admin@e2e.test', password = 'e2epass123') {
  await page.goto('/login')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Пароль').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
  await expect(page).toHaveURL(/\/experiments$/)
}

/** Реальный design через API (dataset upload -> POST /design -> poll job) —
 * та же цепочка, что backend/tests/test_design_job.py, здесь для сидинга
 * данных под e2e-тесты списка/детали/удаления (не мокаем бэкенд). */
export async function seedExperiment(
  request: APIRequestContext,
  name: string,
  opts: { email?: string; password?: string } = {},
): Promise<void> {
  const email = opts.email ?? 'admin@e2e.test'
  const password = opts.password ?? 'e2epass123'

  const loginResp = await request.post(`${API_BASE}/auth/login`, { data: { email, password } })
  if (!loginResp.ok()) throw new Error(`login failed: ${loginResp.status()}`)

  const lines = ['user_id,revenue'].concat(
    Array.from({ length: 200 }, (_, i) => `u_${name}_${i},${100 + (i % 10)}`),
  )
  const uploadResp = await request.post(`${API_BASE}/datasets`, {
    multipart: {
      kind: 'pre_design',
      file: { name: 'data.csv', mimeType: 'text/csv', buffer: Buffer.from(lines.join('\n')) },
    },
  })
  if (!uploadResp.ok()) throw new Error(`upload failed: ${uploadResp.status()}`)
  const dataset = await uploadResp.json()

  const designResp = await request.post(`${API_BASE}/design`, {
    data: {
      config: {
        name,
        unit_col: 'user_id',
        groups: { control: 0.5, treatment: 0.5 },
        metrics: [{ name: 'revenue', type: 'continuous', role: 'primary' }],
        sample_size: 200,
        split_method: 'simple',
        isolation: 'off',
      },
      dataset_id: dataset.id,
    },
  })
  if (!designResp.ok()) throw new Error(`design submit failed: ${designResp.status()}`)
  const { job_id } = await designResp.json()

  for (let i = 0; i < 100; i++) {
    const jobResp = await request.get(`${API_BASE}/jobs/${job_id}`)
    const job = await jobResp.json()
    if (job.status === 'completed') return
    if (job.status === 'failed') throw new Error(`design job failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error('design job did not finish in time')
}
