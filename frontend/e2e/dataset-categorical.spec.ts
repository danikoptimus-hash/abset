import { test, expect, type APIRequestContext } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

async function pollJob(request: APIRequestContext, jobId: string) {
  for (let i = 0; i < 100; i++) {
    const job = await (await request.get(`${API_BASE}/jobs/${jobId}`)).json()
    if (job.status === 'completed') return
    if (job.status === 'failed') throw new Error(`job failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error('job did not finish')
}

// Part 2: an integer column with a few meaningful values (months_ago ∈ {1..5})
// must stratify per-value, not into pandas interval bins. The Edit modal shows
// it flagged categorical (heuristic), and designing with it as a stratum
// produces one stratum per raw value with clean labels.
test('categorical flag: integer column shows per-value strata, editable in Edit dataset', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)

  const rows = ['user_id,revenue,months_ago']
  let uid = 0
  for (const months of [1, 2, 3, 4, 5]) {
    for (let i = 0; i < 40; i++) rows.push(`u${uid++},${100 + (uid % 7)},${months}`)
  }
  const filename = `strata_cat_${Date.now()}.csv`
  const { id: datasetId } = await uploadDataset(request, rows.join('\n'), filename)

  // Edit dataset UI: the Column types editor shows months_ago flagged
  // categorical (the heuristic default for a low-cardinality integer).
  await loginViaUi(page)
  await page.goto('/datasets')
  await page.getByRole('row', { name: new RegExp(filename.replace('.', '\\.')) })
    .getByRole('button', { name: 'Edit' })
    .click()
  await page.getByText('Column types (categorical vs binned)').click()
  const monthsCheckbox = page.getByRole('checkbox', { name: 'categorical-months_ago' })
  await expect(monthsCheckbox).toBeChecked()
  // Toggle off and back on to exercise the control, then save.
  await monthsCheckbox.click()
  await expect(monthsCheckbox).not.toBeChecked()
  await monthsCheckbox.click()
  await expect(monthsCheckbox).toBeChecked()
  await page.getByRole('button', { name: 'Save' }).click()

  // Design (via API for speed) with months_ago as a stratum.
  const name = `cat_strata_e2e_${Date.now()}`
  const designResp = await request.post(`${API_BASE}/design`, {
    data: {
      config: {
        name, unit_col: 'user_id',
        groups: { control: 0.5, treatment: 0.5 },
        metrics: [{ name: 'revenue', type: 'continuous', role: 'primary' }],
        sample_size: 160, split_method: 'stratified', isolation: 'off',
        strata: ['months_ago'],
      },
      dataset_id: datasetId,
    },
  })
  if (!designResp.ok()) throw new Error(`design failed: ${designResp.status()}`)
  await pollJob(request, (await designResp.json()).job_id)

  // The design report's stratum table has one row per raw value ("1".."5")
  // and NO raw pandas interval syntax.
  const report = await (await request.get(`${API_BASE}/experiments/${name}/reports/design_report.html`)).text()
  expect(report).toContain('Stratified by: <strong>months_ago</strong>')
  expect(report).toContain('<td>5</td>')
  expect(report).not.toContain('(0.999')
  expect(report).not.toContain(', 2.0]')
})
