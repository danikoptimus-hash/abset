import { test, expect, type Page } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

// AntD keeps a closed Select's dropdown in the DOM (display:none via
// .ant-select-dropdown-hidden) rather than unmounting it, so with several
// per-row "map-<value>" selects on the page, a plain getByTitle(optionLabel)
// matches one stale, invisible option per previously-opened dropdown in
// addition to the live one — scope to the one dropdown that's actually
// visible right now.
async function selectComboboxOption(page: Page, comboboxName: string, optionLabel: string) {
  await page.getByRole('combobox', { name: comboboxName }).click()
  // The most recently opened dropdown is the last one AntD appended to the
  // document body — earlier ones stick around (hidden, but still matched by
  // getByTitle) instead of unmounting.
  await page.locator('.ant-select-dropdown').last().getByTitle(optionLabel, { exact: true }).click()
}

// Item 12: external split (e.g. Firebase A/B Testing) — the split happens
// outside ABSet; the wizard only collects the declared config (no dataset),
// and analysis requires mapping a group column in the uploaded post-data to
// the declared groups before it can run.
test('create an external-split experiment via the wizard, map groups, and analyze end-to-end', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `external_e2e_${Date.now()}`

  const csv =
    'variant,conversion\n' +
    Array.from({ length: 20 }, (_, i) => `A,${10 + (i % 5)}`).join('\n') +
    '\n' +
    Array.from({ length: 20 }, (_, i) => `B,${15 + (i % 5)}`).join('\n') +
    '\n' +
    Array.from({ length: 5 }, (_, i) => `C,${20 + i}`).join('\n')
  const filename = `external_post_${Date.now()}.csv`
  await uploadDataset(request, csv, filename)

  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  // Step 1 (Data): switch to External split — the split-agnostic path with
  // no reference dataset selected stays fully manual (free-text columns).
  await page.getByText('External split (e.g. Firebase)', { exact: true }).click()
  await expect(page.getByText('No dataset required for an external split')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Demo Data' })).not.toBeVisible()
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 2 (Groups & Metrics): name, hypothesis, and a free-text metric
  // column name (no dataset to pick a column from).
  await page.getByPlaceholder('Experiment name').fill(name)
  await page.getByLabel('Hypothesis').fill('External split hypothesis for e2e.')
  await page.getByPlaceholder('Data column name, e.g. conversion').fill('conversion')
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 3 (Parameters): simplified to just an optional expected sample size.
  await expect(page.getByText('Expected sample size (optional)')).toBeVisible()
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 4 (Review): submit.
  await expect(page.getByText('External split (e.g. Firebase)')).toBeVisible()
  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${name}$`), { timeout: 20_000 })

  // Header badge + no samples download on the Design tab.
  await expect(page.getByText('External split', { exact: true })).toBeVisible()
  await expect(page.getByText('external design: power calculated by the external system', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Download Samples (ZIP)' })).not.toBeVisible()

  // Analysis tab: no demo-data button (no assignments to generate from).
  await page.getByRole('tab', { name: 'Analysis' }).click()
  await expect(page.getByRole('button', { name: /Generate demo post-period data/ })).not.toBeVisible()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible()

  // Group assignment: pick the column, then map each value.
  await expect(page.getByText('Group assignment')).toBeVisible()
  const runButton = page.getByRole('button', { name: 'Run analysis' })
  await expect(runButton).toBeDisabled()

  await selectComboboxOption(page, 'group-column-select', 'variant')

  await expect(page.getByRole('cell', { name: 'A', exact: true })).toBeVisible()
  await selectComboboxOption(page, 'map-A', 'control')
  await selectComboboxOption(page, 'map-B', 'treatment')
  await selectComboboxOption(page, 'map-C', 'Exclude')

  await expect(runButton).toBeEnabled()
  await runButton.click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // The excluded-rows warning stands in for the "loss vs assignments"
  // sanity check, which doesn't apply here (no assignments to compare
  // against) — shown right on the Analysis tab, same as the Results tab.
  await expect(page.getByText(/Group column coverage: 5 of 45 rows/)).toBeVisible()
})

// External split rework: the reference-dataset path — pick columns from
// dropdowns (no free-text), sample size auto-fills, declare a stratum, then
// analyze and see the per-segment (country) breakdown with distinct lifts.
test('external split with a reference dataset: column pickers, auto sample size, strata, and per-segment analysis', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000)
  const name = `external_ref_e2e_${Date.now()}`

  // One dataset serves as BOTH the design reference AND the analysis input.
  // US treatment ~+30% over control; UK ~flat — so the country breakdown has
  // clearly distinct lifts. Non-constant values keep per-segment variance
  // non-zero (no degenerate stratum).
  const line = (variant: string, value: number, country: string) => `${variant},${value},${country}`
  const rows: string[] = ['variant,value,country']
  for (let i = 0; i < 60; i++) rows.push(line('A', 100 + (i % 5), 'US'))
  for (let i = 0; i < 60; i++) rows.push(line('B', 130 + (i % 5), 'US'))
  for (let i = 0; i < 60; i++) rows.push(line('A', 100 + (i % 5), 'UK'))
  for (let i = 0; i < 60; i++) rows.push(line('B', 101 + (i % 5), 'UK'))
  const csv = rows.join('\n')
  const filename = `external_ref_${Date.now()}.csv`
  await uploadDataset(request, csv, filename)

  await loginViaUi(page)
  await page.getByRole('button', { name: 'Create A/B Test' }).click()

  // Step 1: external split + select the reference dataset.
  await page.getByText('External split (e.g. Firebase)', { exact: true }).click()
  const refSelect = page.getByRole('combobox', { name: 'reference-dataset-select' })
  await refSelect.click()
  await refSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(/Reference loaded: 240 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 2: with a reference dataset, the metric column is a DROPDOWN — the
  // free-text input must NOT be present.
  await page.getByPlaceholder('Experiment name').fill(name)
  await page.getByLabel('Hypothesis').fill('External split with reference dataset.')
  await expect(page.getByPlaceholder('Data column name, e.g. conversion')).toHaveCount(0)
  await page.locator('.ant-select', { hasText: 'Dataframe column' }).click()
  await page.getByTitle('value', { exact: true }).click()
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 3: expected sample size auto-filled from the reference row count,
  // plus declare `country` as a stratum/segment column.
  await expect(page.getByRole('spinbutton')).toHaveValue('240')
  const strataSelect = page.getByRole('combobox', { name: 'external-strata-select' })
  await strataSelect.click()
  await page.locator('.ant-select-dropdown').last().getByTitle('country', { exact: true }).click()
  // A multi-select keeps its dropdown open after a pick; close it so the
  // still-open option list can't intercept the Next click below.
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 4: submit.
  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${name}$`), { timeout: 20_000 })

  // Analysis: the reference dataset is pre-selected as the post-period data.
  await page.getByRole('tab', { name: 'Analysis' }).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible({
    timeout: 15_000,
  })

  await selectComboboxOption(page, 'group-column-select', 'variant')
  await selectComboboxOption(page, 'map-A', 'control')
  await selectComboboxOption(page, 'map-B', 'treatment')

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // The external analysis now produces a strata balance table and a
  // per-segment (country) breakdown — neither existed for external before.
  await expect(page.getByText('Stratum balance', { exact: false })).toBeVisible()
  await expect(page.getByText(/By country/).first()).toBeVisible()
})

// Item 12.5: hypothesis family (primary metrics × treatment groups) reuses
// the same smart-visibility logic for external experiments as for ABSet
// ones — a 3-value mapping (1 control + 2 treatment groups) must surface
// the correction selector, unlike the 2-group case above where it's hidden.
test('correction selector appears when an external experiment declares more than one treatment group', async ({
  page,
  request,
}) => {
  test.setTimeout(30_000)
  const name = `external_correction_e2e_${Date.now()}`

  const loginResp = await request.post(`${API_BASE}/auth/login`, {
    data: { email: 'admin@e2e.test', password: 'e2epass123' },
  })
  if (!loginResp.ok()) throw new Error(`login failed: ${loginResp.status()}`)

  const designResp = await request.post(`${API_BASE}/design`, {
    data: {
      config: {
        name, unit_col: '',
        groups: { control: 0.34, treatment_a: 0.33, treatment_b: 0.33 },
        metrics: [{ name: 'conversion', type: 'binary', role: 'primary' }],
        split_source: 'external', isolation: 'off',
      },
    },
  })
  if (!designResp.ok()) throw new Error(`design submit failed: ${designResp.status()}`)
  const { job_id } = await designResp.json()
  for (let i = 0; i < 50; i++) {
    const jobResp = await request.get(`${API_BASE}/jobs/${job_id}`)
    const job = await jobResp.json()
    if (job.status === 'completed') break
    if (job.status === 'failed') throw new Error(`design job failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 100))
  }

  const csv =
    'variant,conversion\n' +
    Array.from({ length: 10 }, (_, i) => `A,${i % 2}`).join('\n') +
    '\n' +
    Array.from({ length: 10 }, (_, i) => `B,${i % 2}`).join('\n') +
    '\n' +
    Array.from({ length: 10 }, (_, i) => `C,${i % 2}`).join('\n')
  const filename = `external_3group_post_${Date.now()}.csv`
  await uploadDataset(request, csv, filename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible()

  await selectComboboxOption(page, 'group-column-select', 'variant')
  await selectComboboxOption(page, 'map-A', 'control')
  await selectComboboxOption(page, 'map-B', 'treatment_a')
  await selectComboboxOption(page, 'map-C', 'treatment_b')

  // Item 3 (consolidated package): correction now lives in the main options
  // flow (no "Advanced options" collapse to open anymore).
  await expect(page.getByText(/Your design tests 2 hypotheses/)).toBeVisible()
  await expect(page.getByText('Multiple testing correction')).toBeVisible()
})
