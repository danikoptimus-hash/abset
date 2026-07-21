import { test, expect, type APIRequestContext } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

async function designWithManyStrata(request: APIRequestContext, name: string, datasetId: string) {
  const resp = await request.post(`${API_BASE}/design`, {
    data: {
      config: {
        name, unit_col: 'user_id',
        groups: { control: 0.5, treatment: 0.5 },
        metrics: [{ name: 'revenue', type: 'continuous', role: 'primary' }],
        sample_size: 520, split_method: 'stratified', isolation: 'off',
        strata: ['seg'],
      },
      dataset_id: datasetId,
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

// Visibility package §1: the strata power check on the Design tab, collapsed by
// default with a summary on a many-strata (> 12) experiment, expandable, and
// the expand state persists across navigation (per-user preference).
test('Design tab strata power check: collapsed with summary, expands, survives navigation', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)

  // 13 string strata (> 12 → collapsed by default).
  const rows = ['user_id,revenue,seg']
  let uid = 0
  for (let s = 0; s < 13; s++) {
    for (let i = 0; i < 40; i++) rows.push(`u${uid++},${100 + (uid % 11)},s${String(s).padStart(2, '0')}`)
  }
  const filename = `strata_power_tab_${Date.now()}.csv`
  const { id: datasetId } = await uploadDataset(request, rows.join('\n'), filename)

  const name = `strata_power_tab_e2e_${Date.now()}`
  await designWithManyStrata(request, name, datasetId)

  // Reset the shared account's pref so "collapsed by default" holds even on a
  // retry (uploadDataset already logged this request context in as admin).
  await request.patch(`${API_BASE}/auth/me/preferences`, { data: { strata_power_expanded: false } })

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  // Summary always visible; collapsed by default → the "s00" stratum cell hidden.
  await expect(page.getByText(/Strata power check: 13 strata/)).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('cell', { name: 's00', exact: true })).toBeHidden()

  // Expand → the stratum rows appear.
  await page.getByText(/Strata power check: 13 strata/).click()
  await expect(page.getByRole('cell', { name: 's00', exact: true })).toBeVisible()

  // State persists across navigation (per-user strata_power_expanded).
  await page.goto('/experiments')
  await page.goto(`/experiments/${name}`)
  await expect(page.getByText(/Strata power check: 13 strata/)).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('cell', { name: 's00', exact: true })).toBeVisible()
})
