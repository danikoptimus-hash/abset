import { test, expect, type Page, type APIRequestContext, type Locator } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

// AntD keeps closed dropdowns in the DOM (hidden), so with several selects on
// the page a global option locator matches stale options too — scope to the
// dropdown that's actually open right now (the last one AntD appended).
async function pickOption(page: Page, combobox: Locator, optionLabel: string) {
  await combobox.click()
  await page.locator('.ant-select-dropdown').last().getByTitle(optionLabel, { exact: true }).click()
}

async function login(request: APIRequestContext) {
  const r = await request.post(`${API_BASE}/auth/login`, {
    data: { email: 'admin@e2e.test', password: 'e2epass123' },
  })
  if (!r.ok()) throw new Error(`login failed: ${r.status()}`)
}

async function designExternal(request: APIRequestContext, name: string, strata: string[]) {
  const resp = await request.post(`${API_BASE}/design`, {
    data: {
      config: {
        name, unit_col: '',
        groups: { control: 0.5, treatment: 0.5 },
        metrics: [{ name: 'value', type: 'continuous', role: 'primary' }],
        split_source: 'external', isolation: 'off', strata,
      },
    },
  })
  if (!resp.ok()) throw new Error(`design failed: ${resp.status()}`)
  const { job_id } = await resp.json()
  for (let i = 0; i < 80; i++) {
    const job = await (await request.get(`${API_BASE}/jobs/${job_id}`)).json()
    if (job.status === 'completed') return
    if (job.status === 'failed') throw new Error(`design job failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error('design job did not finish')
}

async function pickGroupMapping(page: Page) {
  await pickOption(page, page.getByRole('combobox', { name: 'group-column-select' }), 'variant')
  await pickOption(page, page.getByRole('combobox', { name: 'map-A' }), 'control')
  await pickOption(page, page.getByRole('combobox', { name: 'map-B' }), 'treatment')
}

// §1 (pre-run combination) + §2 (post-hoc cut) end to end.
test('declare a country × gender combination before running, then add a cut post-hoc', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000)
  const name = `segments_e2e_${Date.now()}`

  // Powered cells (120/group per country×gender) so the crossed forest renders.
  const rows = ['variant,value,country,gender']
  for (const country of ['US', 'UK']) {
    for (const gender of ['M', 'F']) {
      for (let i = 0; i < 120; i++) {
        rows.push(`A,${100 + (i % 5)},${country},${gender}`)
        rows.push(`B,${130 + (i % 5)},${country},${gender}`)
      }
    }
  }
  const filename = `segments_${Date.now()}.csv`
  await login(request)
  await designExternal(request, name, ['country'])
  await uploadDataset(request, rows.join('\n'), filename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible({
    timeout: 15_000,
  })

  await pickGroupMapping(page)

  // Add a country × gender combination via the shared cut picker.
  const comboSelect = page.getByRole('combobox', { name: 'segment-combination-select' })
  await comboSelect.click()
  await page.locator('.ant-select-dropdown').last().getByTitle('country', { exact: true }).click()
  await page.locator('.ant-select-dropdown').last().getByTitle('gender', { exact: true }).click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Add' }).click()
  await expect(page.getByText(/country × gender \(4 cells\)/)).toBeVisible()

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // The crossed cut renders (as a "Segment by" option and its own block).
  await expect(page.getByText('country × gender').first()).toBeVisible()

  // §2: add a post-hoc cut on the finished run, from the Results tab.
  await page.getByRole('tab', { name: 'Results' }).click()
  await page.getByRole('button', { name: 'Analyze segments' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('combobox', { name: 'segment-columns-select' }).click()
  await page.locator('.ant-select-dropdown').last().getByTitle('gender', { exact: true }).click()
  await page.keyboard.press('Escape')
  await dialog.getByRole('button', { name: 'Add segments' }).click()

  // The post-hoc "gender" cut appears, tagged and removable. Both the
  // Analysis and Results tab panels render the same run (shared query cache),
  // so scope to the Results panel to avoid a strict-mode double match.
  const resultsPanel = page.getByLabel('Results')
  await expect(resultsPanel.getByText('added post-hoc')).toBeVisible({ timeout: 20_000 })
  await expect(resultsPanel.getByText(/By gender/).first()).toBeVisible()
})

// Bug (confirmed on a live experiment): the rendered segment set must come
// from the REQUEST, not the design declaration — an undeclared column added on
// Analyze must appear (badged "ad-hoc"), and the all-strata auto-cross the
// declaration used to fabricate must NOT appear unless requested.
test('an undeclared column added on Analyze appears as ad-hoc; no auto-cross of declared strata', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000)
  const name = `adhoc_no_autocross_e2e_${Date.now()}`

  // Two declared strata (country, plan) — before the fix these would produce a
  // fabricated "country × plan" cross. Plus an undeclared categorical 'channel'.
  const rows = ['variant,value,country,plan,channel']
  for (const country of ['US', 'UK']) {
    for (const plan of ['free', 'pro']) {
      for (const channel of ['push', 'sms']) {
        for (let i = 0; i < 80; i++) {
          rows.push(`A,${100 + (i % 5)},${country},${plan},${channel}`)
          rows.push(`B,${130 + (i % 5)},${country},${plan},${channel}`)
        }
      }
    }
  }
  const filename = `adhoc_${Date.now()}.csv`
  await login(request)
  await designExternal(request, name, ['country', 'plan'])
  await uploadDataset(request, rows.join('\n'), filename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible({
    timeout: 15_000,
  })

  await pickGroupMapping(page)

  // Add the undeclared 'channel' to the single-columns picker (country, plan
  // are pre-filled from the declared strata).
  await pickOption(page, page.getByRole('combobox', { name: 'segment-columns-select' }), 'channel')
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // The "Segment by" toggle offers each requested column individually...
  const segmented = page.locator('.ant-segmented').first()
  await expect(segmented.getByText('channel', { exact: true })).toBeVisible()
  await expect(segmented.getByText('country', { exact: true })).toBeVisible()
  await expect(segmented.getByText('plan', { exact: true })).toBeVisible()
  // ...and NOT the fabricated auto-cross of the declared strata.
  await expect(page.getByText('country × plan')).toHaveCount(0)

  // Selecting 'channel' shows it badged ad-hoc (not silently dropped).
  await segmented.getByText('channel', { exact: true }).click()
  await expect(page.getByText('ad-hoc (not declared at design)')).toBeVisible()
})

// §3: a many-strata balance table is collapsed by default with a summary line.
test('strata balance table collapses with a summary on a many-strata run', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `balance_collapse_e2e_${Date.now()}`

  const rows = ['variant,value,country']
  for (let c = 0; c < 13; c++) {
    for (let i = 0; i < 40; i++) {
      rows.push(`A,${100 + (i % 5)},C${c}`)
      rows.push(`B,${101 + (i % 5)},C${c}`)
    }
  }
  const filename = `balance_${Date.now()}.csv`
  await login(request)
  await designExternal(request, name, ['country'])
  const { id: datasetId } = await uploadDataset(request, rows.join('\n'), filename)

  // Analyze headlessly (13 strata), then check the Results tab.
  const analyzeResp = await request.post(`${API_BASE}/experiments/${name}/analyze`, {
    data: {
      dataset_id: datasetId, correction: 'none',
      group_column: 'variant', group_mapping: { A: 'control', B: 'treatment' },
      segment_columns: ['country'],
    },
  })
  if (!analyzeResp.ok()) throw new Error(`analyze failed: ${analyzeResp.status()}`)
  const { job_id } = await analyzeResp.json()
  for (let i = 0; i < 100; i++) {
    const job = await (await request.get(`${API_BASE}/jobs/${job_id}`)).json()
    if (job.status === 'completed') break
    if (job.status === 'failed') throw new Error(`analyze job failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 100))
  }

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Results' }).click()

  // Summary always visible; the per-stratum rows are hidden until expanded.
  await expect(page.getByText(/13 strata · balance chi-square p=/)).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('cell', { name: 'C00', exact: true })).toBeHidden()
})
