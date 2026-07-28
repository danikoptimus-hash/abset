import { test, expect, type Page, type APIRequestContext, type Locator } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

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

async function selectAndMap(page: Page, filename: string) {
  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible({ timeout: 15_000 })
  await pickOption(page, page.getByRole('combobox', { name: 'group-column-select' }), 'variant')
  await pickOption(page, page.getByRole('combobox', { name: 'map-A' }), 'control')
  await pickOption(page, page.getByRole('combobox', { name: 'map-B' }), 'treatment')
}

// The reported confusion: the report on screen is a PREVIOUS run, but the form
// has new selections — with no indication, it looks like a column was dropped.
// Now: the Analyze form warns when its selection differs from the displayed
// run, the Results tab states the run's segment set, and re-running applies it.
test('form flags a segment selection not yet run; Results states the run segment set', async ({ page, request }) => {
  test.setTimeout(120_000)
  const name = `staleness_e2e_${Date.now()}`
  const rows = ['variant,value,channel']
  for (const ch of ['organic', 'paid']) {
    for (let i = 0; i < 150; i++) {
      rows.push(`A,${100 + (i % 5)},${ch}`)
      rows.push(`B,${130 + (i % 5)},${ch}`)
    }
  }
  const filename = `staleness_${Date.now()}.csv`
  await login(request)
  await designExternalNoStrata(request, name)
  await uploadDataset(request, rows.join('\n'), filename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  // First run WITHOUT any explicit segment cut (a "default" run).
  await selectAndMap(page, filename)
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // Results tab states which run it shows and its segment set.
  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByText(/run #1/)).toBeVisible()
  await expect(page.getByText(/Segments: design-declared strata/)).toBeVisible()

  // Back on Analysis: re-open the form, add 'channel' — the form must warn that
  // this selection has NOT been run yet (so a stale report is not mistaken for
  // a dropped column).
  await page.getByRole('tab', { name: 'Analysis' }).click()
  await page.getByRole('button', { name: 'Re-run analysis' }).click()
  await selectAndMap(page, filename)
  await pickOption(page, page.getByRole('combobox', { name: 'segment-columns-select' }), 'channel')
  await page.keyboard.press('Escape')
  await expect(page.getByText("This segment selection hasn't been run yet")).toBeVisible()

  // Re-run applies it: the warning clears and the run now carries 'channel'.
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText("This segment selection hasn't been run yet")).toHaveCount(0)

  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByText(/run #2/)).toBeVisible()
  await expect(page.getByText(/Segments: channel/)).toBeVisible()
})
