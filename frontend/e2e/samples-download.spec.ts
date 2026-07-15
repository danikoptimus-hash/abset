import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

// Item 6: separate per-group CSV download buttons (using actual group
// names) alongside the combined ZIP — motivation: the product team only
// gets handed the treatment file for rollout, and a combined ZIP risks
// shipping control users along with it. Verifies both buttons exist with
// the real group names and that each delivers exactly that group's rows
// (not the other group's, not a mix).
test('Design tab has separate per-group CSV download buttons delivering the correct row sets', async ({
  page,
  request,
}) => {
  const name = `samples_download_e2e_${Date.now()}`
  await seedExperiment(request, name) // groups: control/treatment, 50/50, 200 rows

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Design' }).click()

  const controlButton = page.getByRole('link', { name: 'Download control.csv' })
  const treatmentButton = page.getByRole('link', { name: 'Download treatment.csv' })
  await expect(controlButton).toBeVisible()
  await expect(treatmentButton).toBeVisible()
  // The combined ZIP stays available as a third option, not replaced.
  await expect(page.getByRole('link', { name: 'Download Samples (ZIP)' })).toBeVisible()

  const controlHref = await controlButton.getAttribute('href')
  const treatmentHref = await treatmentButton.getAttribute('href')
  expect(controlHref).toBe(`/api/v1/experiments/${name}/samples/control.csv`)
  expect(treatmentHref).toBe(`/api/v1/experiments/${name}/samples/treatment.csv`)

  const controlResp = await page.request.get(controlHref!)
  const treatmentResp = await page.request.get(treatmentHref!)
  expect(controlResp.ok()).toBeTruthy()
  expect(treatmentResp.ok()).toBeTruthy()

  const controlRows = (await controlResp.text()).trim().split('\n')
  const treatmentRows = (await treatmentResp.text()).trim().split('\n')
  const controlHeader = controlRows[0]
  const treatmentBody = treatmentRows.slice(1)
  const controlBody = controlRows.slice(1)

  expect(controlHeader).toContain('group')
  // Every data row in control.csv is actually a control-group row, and none
  // of them appear in treatment.csv (disjoint, not a copy/mix of both).
  expect(controlBody.length).toBeGreaterThan(0)
  expect(treatmentBody.length).toBeGreaterThan(0)
  expect(controlBody.every((r) => r.includes(',control,'))).toBe(true)
  expect(treatmentBody.every((r) => r.includes(',treatment,'))).toBe(true)
  const controlIds = new Set(controlBody.map((r) => r.split(',')[0]))
  const treatmentIds = new Set(treatmentBody.map((r) => r.split(',')[0]))
  expect([...controlIds].some((id) => treatmentIds.has(id))).toBe(false)
  // The two files together account for every seeded unit.
  expect(controlIds.size + treatmentIds.size).toBe(200)
})
