import { test, expect } from '@playwright/test'
import type { APIRequestContext } from '@playwright/test'
import { loginViaUi } from './helpers'

// Пакет design & reporting fixes (A1-A5 / B1-B3 / C1-C4) — сквозные проверки
// того, что ТЗ просит увидеть глазами: визард с именованными метриками и
// исключением пересечения, авто-завершённый тест (статус + строка в History),
// заголовок скачанной выборки, гипотеза в отчёте.

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

async function apiLogin(request: APIRequestContext) {
  const resp = await request.post(`${API_BASE}/auth/login`, {
    data: { email: 'admin@e2e.test', password: 'e2epass123' },
  })
  if (!resp.ok()) throw new Error(`login failed: ${resp.status()}`)
}

/** Датасет, чья ID-колонка НЕ называется unit_id — в этом весь смысл C1. */
async function uploadClientIdDataset(
  request: APIRequestContext,
  tag: string,
  rows = 200,
): Promise<string> {
  const lines = ['client_id,revenue,country'].concat(
    Array.from(
      { length: rows },
      (_, i) => `c_${tag}_${i},${100 + (i % 10)},${i % 2 ? 'ru' : 'us'}`,
    ),
  )
  const resp = await request.post(`${API_BASE}/datasets`, {
    multipart: {
      kind: 'pre_design',
      file: { name: `${tag}.csv`, mimeType: 'text/csv', buffer: Buffer.from(lines.join('\n')) },
    },
  })
  if (!resp.ok()) throw new Error(`upload failed: ${resp.status()}`)
  return (await resp.json()).id as string
}

async function designViaApi(
  request: APIRequestContext,
  name: string,
  datasetId: string,
  extra: Record<string, unknown> = {},
  configExtra: Record<string, unknown> = {},
): Promise<void> {
  const resp = await request.post(`${API_BASE}/design`, {
    data: {
      config: {
        name,
        unit_col: 'client_id',
        groups: { control: 0.5, treatment: 0.5 },
        metrics: [{ name: 'revenue', type: 'continuous', role: 'primary' }],
        sample_size: 200,
        split_method: 'simple',
        isolation: 'off',
        ...configExtra,
      },
      dataset_id: datasetId,
      ...extra,
    },
  })
  if (!resp.ok()) throw new Error(`design submit failed: ${resp.status()}`)
  const { job_id } = await resp.json()
  for (let i = 0; i < 200; i++) {
    const job = await (await request.get(`${API_BASE}/jobs/${job_id}`)).json()
    if (job.status === 'completed') return
    if (job.status === 'failed') throw new Error(`design failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error('design job did not finish in time')
}

test('wizard: named metric, planned end date, and the overlap-exclusion path', async ({
  page,
  request,
}) => {
  const suffix = Date.now()
  const occupying = `_dev_e2e_occupy_${suffix}`
  const name = `_dev_e2e_wizard_${suffix}`

  await apiLogin(request)
  // Занимаем юзеров другим активным тестом — иначе пересечению взяться неоткуда.
  const sharedTag = `shared_${suffix}`
  const occupyingDataset = await uploadClientIdDataset(request, sharedTag, 120)
  await designViaApi(request, occupying, occupyingDataset)
  // Тот же диапазон ID -> гарантированное пересечение по 120 юзерам.
  const wizardDataset = await uploadClientIdDataset(request, sharedTag, 200)

  await loginViaUi(page)
  await page.goto('/experiments/new')

  // --- Step 1: existing dataset ---
  await page.getByText('Select an existing dataset').first().click().catch(() => {})
  const datasetSelect = page.locator('.ant-select').first()
  await datasetSelect.click()
  await page.keyboard.type(sharedTag)
  await page.waitForTimeout(600)
  await page.locator('.ant-select-item-option').last().click()
  await page.getByRole('button', { name: 'Next' }).click()

  // --- Step 2: name, metric display name, planned end date ---
  await page.getByPlaceholder('Experiment name').fill(name)
  await page.getByLabel('Hypothesis').fill('Named metrics render everywhere')

  // A1: подпись метрики отдельно от колонки данных.
  const metricColumn = page.locator('.ant-card .ant-select').nth(1)
  await metricColumn.click()
  await page.locator('.ant-select-item-option-content').filter({ hasText: /^revenue$/ }).click()
  await page.getByPlaceholder(/Metric name/).fill('Revenue per user')

  // B2: плановая дата окончания.
  await page.getByLabel('Planned end date').click()
  await page.keyboard.type('2031-12-31')
  await page.keyboard.press('Enter')

  await page.getByRole('button', { name: 'Next' }).click()

  // --- Step 3: isolation=warn, чтобы получить диалог пересечения ---
  const isolationSelect = page.locator('.ant-select').filter({ hasText: 'exclude' }).last()
  await isolationSelect.click()
  await page.locator('.ant-select-item-option-content').filter({ hasText: /^warn/ }).click()
  await page.getByRole('button', { name: 'Calculate sample size' }).click()
  await expect(page.getByText(/eligible users/)).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  // --- Step 4: обе кнопки на пересечении, выбираем "исключить" ---
  await expect(page.getByText('Revenue per user')).toBeVisible()
  await page.getByRole('button', { name: 'Design', exact: true }).click()

  await expect(page.getByText('Overlap detected with other active experiments')).toBeVisible({
    timeout: 60_000,
  })
  // A3: действий ровно два, и исключение — основное.
  await expect(page.getByRole('button', { name: /Continue despite the overlap/ })).toBeVisible()
  await page.getByRole('button', { name: /Exclude overlapping & continue/ }).click()

  await expect(page).toHaveURL(new RegExp(`/experiments/${name}$`), { timeout: 90_000 })

  // C3: решение видно на Design tab, а не только в момент нажатия.
  await page.getByRole('tab', { name: 'Design' }).click()
  await expect(page.getByText(/Excluded \d+ overlapping users/)).toBeVisible()
  // A1: подпись метрики + техническая колонка рядом.
  await expect(page.getByText('Revenue per user').first()).toBeVisible()
  await expect(page.getByText('column: revenue').first()).toBeVisible()
  // B2: плановая дата в шапке.
  await expect(page.getByText(/Planned end/)).toBeVisible()
})

test('downloaded sample keeps the original id column name', async ({ page, request }) => {
  const name = `_dev_e2e_c1_${Date.now()}`
  await apiLogin(request)
  const datasetId = await uploadClientIdDataset(request, `c1_${Date.now()}`)
  await designViaApi(request, name, datasetId)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Design' }).click()

  const download = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('link', { name: /Download control\.csv/ }).click(),
  ]).then(([d]) => d)

  const stream = await download.createReadStream()
  const chunks: Buffer[] = []
  for await (const chunk of stream) chunks.push(chunk as Buffer)
  const header = Buffer.concat(chunks).toString('utf-8').split('\n')[0].trim()

  // C1: имя из датасета, а не внутреннее unit_id; и никаких служебных колонок.
  expect(header).toBe('client_id,group')
})

test('analysis report carries the hypothesis and the design context', async ({ page, request }) => {
  const name = `_dev_e2e_c2_${Date.now()}`
  await apiLogin(request)
  const datasetId = await uploadClientIdDataset(request, `c2_${Date.now()}`)
  await designViaApi(request, name, datasetId, {}, {
    metrics: [
      {
        name: 'revenue',
        display_name: 'Revenue per user',
        type: 'continuous',
        role: 'primary',
        description: 'Sum of paid orders',
      },
    ],
  })

  // Гипотеза сохраняется отдельным вызовом — ровно как это делает визард.
  const blocks = await (await request.get(`${API_BASE}/experiments/${name}/blocks`)).json()
  const hypothesis = blocks.find((b: { kind: string }) => b.kind === 'hypothesis')
  await request.put(`${API_BASE}/experiments/${name}/blocks`, {
    data: [{ ...hypothesis, content_md: 'Redesigned checkout lifts revenue' }],
  })

  // Анализ на тех же данных.
  const postId = await uploadClientIdDataset(request, `c2post_${Date.now()}`)
  const analyzeResp = await request.post(`${API_BASE}/experiments/${name}/analyze`, {
    data: { dataset_id: postId, correction: 'holm' },
  })
  expect(analyzeResp.ok()).toBeTruthy()
  const { job_id } = await analyzeResp.json()
  for (let i = 0; i < 300; i++) {
    const job = await (await request.get(`${API_BASE}/jobs/${job_id}`)).json()
    if (job.status === 'completed') break
    if (job.status === 'failed') throw new Error(`analyze failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 200))
  }

  await loginViaUi(page)
  const report = await page.request.get(
    `${API_BASE}/experiments/${name}/reports/report.html`,
  )
  expect(report.ok()).toBeTruthy()
  const html = await report.text()

  // C2: гипотезы тут раньше не было вовсе — это и есть зарепорченный баг.
  expect(html).toContain('Redesigned checkout lifts revenue')
  expect(html).toContain('Revenue per user')
  expect(html).toContain('column: revenue')
  expect(html).toContain('Sum of paid orders')
  // A5: кинжала не осталось нигде.
  expect(html).not.toContain('†')
})

test('an experiment past its planned end date auto-completes and says so in History', async ({
  page,
  request,
}) => {
  const name = `_dev_e2e_b3_${Date.now()}`
  await apiLogin(request)
  const datasetId = await uploadClientIdDataset(request, `b3_${Date.now()}`)
  await designViaApi(request, name, datasetId)

  // running + плановая дата в прошлом.
  const statusResp = await request.post(`${API_BASE}/experiments/${name}/status`, {
    data: { to: 'running' },
  })
  expect(statusResp.ok()).toBeTruthy()

  const past = new Date(Date.now() - 3 * 24 * 3600 * 1000).toISOString().slice(0, 10)
  const propsResp = await request.put(`${API_BASE}/experiments/${name}/properties`, {
    data: {
      name,
      owner_ids: [],
      editor_ids: [],
      visible_roles: null,
      set_lifecycle_dates: true,
      started_at: null,
      planned_end_date: past,
    },
  })
  expect(propsResp.ok()).toBeTruthy()

  await loginViaUi(page)
  // B3: ленивая проверка при открытии страницы — статус уже completed, без
  // ожидания фонового тика.
  await page.goto(`/experiments/${name}`)
  await expect(page.getByText('completed').first()).toBeVisible({ timeout: 30_000 })

  await page.getByRole('tab', { name: 'History' }).click()
  await expect(page.getByText('experiment.auto_completed')).toBeVisible()
  await expect(page.getByText(/planned end date reached/)).toBeVisible()
  // Никакого пользователя — это сделала система.
  await expect(page.getByText('system').first()).toBeVisible()
})
