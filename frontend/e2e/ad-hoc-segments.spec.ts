import { test, expect, type Page, type APIRequestContext, type Locator } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

// AntD keeps closed dropdowns in the DOM (hidden) — scope to the open one.
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

async function designExternalNoStrata(request: APIRequestContext, name: string) {
  const resp = await request.post(`${API_BASE}/design`, {
    data: {
      config: {
        name, unit_col: '', groups: { control: 0.5, treatment: 0.5 },
        metrics: [{ name: 'value', type: 'continuous', role: 'primary' }],
        split_source: 'external', isolation: 'off', strata: [],
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

async function selectDatasetAndMap(page: Page, name: string, filename: string) {
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()
  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible({ timeout: 15_000 })
  await pickGroupMapping(page)
}

// An undeclared column ('channel') added on Analyze must NOT be silently
// dropped: it produces its own breakdown, tagged ad-hoc.
test('an undeclared segment column produces an ad-hoc breakdown block', async ({ page, request }) => {
  test.setTimeout(90_000)
  const name = `adhoc_ok_e2e_${Date.now()}`
  const rows = ['variant,value,channel']
  for (const ch of ['organic', 'paid']) {
    for (let i = 0; i < 150; i++) {
      rows.push(`A,${100 + (i % 5)},${ch}`)
      rows.push(`B,${130 + (i % 5)},${ch}`)
    }
  }
  const filename = `adhoc_ok_${Date.now()}.csv`
  await login(request)
  await designExternalNoStrata(request, name)
  await uploadDataset(request, rows.join('\n'), filename)

  await selectDatasetAndMap(page, name, filename)

  // Add the undeclared 'channel' column as a single-column segment cut.
  await pickOption(page, page.getByRole('combobox', { name: 'segment-columns-select' }), 'channel')
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // 'channel' is the sole dimension -> active by default: its block shows,
  // tagged ad-hoc, and no skip notice.
  await expect(page.getByText(/By channel/).first()).toBeVisible()
  await expect(page.getByText(/ad-hoc \(not declared at design\)/)).toBeVisible()
  await expect(page.getByText('Requested segment cuts not shown')).toHaveCount(0)
})

// The bug: a degenerate ad-hoc column (single distinct value) used to vanish
// with no trace. Now it must produce a visible skip notice naming the column.
test('a degenerate ad-hoc segment column produces a visible skip notice, not silence', async ({ page, request }) => {
  test.setTimeout(90_000)
  const name = `adhoc_skip_e2e_${Date.now()}`
  const rows = ['variant,value,channel']
  for (let i = 0; i < 200; i++) {
    rows.push(`A,${100 + (i % 5)},organic`) // single distinct channel value
    rows.push(`B,${130 + (i % 5)},organic`)
  }
  const filename = `adhoc_skip_${Date.now()}.csv`
  await login(request)
  await designExternalNoStrata(request, name)
  await uploadDataset(request, rows.join('\n'), filename)

  await selectDatasetAndMap(page, name, filename)

  await pickOption(page, page.getByRole('combobox', { name: 'segment-columns-select' }), 'channel')
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // Not silently dropped: a notice names the column and the reason.
  await expect(page.getByText('Requested segment cuts not shown')).toBeVisible()
  await expect(page.getByText(/channel.*one distinct value/)).toBeVisible()
})
