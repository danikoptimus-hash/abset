import { test, expect } from '@playwright/test'
import { clickSelectOption, loginViaUi, seedExperiment, uploadDataset } from './helpers'

// Regression for the compare-methods crash bug: a failed analyze job must
// show its real, human-readable error (job.error) instead of the generic
// "Failed to get job status" that used to appear whenever the backend
// process died mid-poll (OOM-killed worker). Triggering a genuine OOM here
// isn't practical in e2e — instead we trigger a deterministic, real
// AnalysisError and check the UI surfaces its actual message.
//
// Trigger: a post-period export with its own "group" column, which collides
// with the assignments join (ref edb716f1) — no duplicate unit ids, so
// item 2's Date-column-required guard doesn't block Run analysis here; a
// duplicate-unit-id trigger was used previously, but that path is now
// caught by the UI itself before the button is even enabled, which would
// make this test about the wrong thing.
test('a failed analyze job shows its real error message, not a generic one', async ({
  page,
  request,
}) => {
  const name = `analyze_fail_e2e_${Date.now()}`
  await seedExperiment(request, name)

  // Uploaded via the API BEFORE navigating: DatasetSelect's query is
  // fetched once on mount and isn't invalidated by an out-of-band API call
  // happening after the page loads.
  const csv =
    'user_id,revenue,group\n' +
    Array.from({ length: 50 }, (_, i) => `u${i},${100},control`).join('\n')
  const collisionFilename = `group_collision_${Date.now()}.csv`
  await uploadDataset(request, csv, collisionFilename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(collisionFilename)
  await page.getByTitle(collisionFilename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${collisionFilename.replace('.', '\\.')}`))).toBeVisible()

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(page.getByText(/collide with ABSet's own/)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Failed to get job status')).not.toBeVisible()
})

// Regression for a real production internal_error report: post-period data
// uploaded without the design's unit-id column crashed with a raw pandas
// KeyError (data[self.config.unit_col] was never guarded) — surfaced as an
// opaque "Internal processing error" instead of a clear, actionable message.
// None of the existing analyze e2e coverage used a post-dataset missing the
// unit_col column, which is exactly why this slipped through untested.
test('analyzing with post-data missing the unit column shows a clear error, not Internal processing error', async ({
  page,
  request,
}) => {
  const name = `analyze_missing_unit_col_e2e_${Date.now()}`
  await seedExperiment(request, name)

  const csv = 'not_user_id,revenue\n' + Array.from({ length: 50 }, (_, i) => `u${i},${100 + (i % 10)}`).join('\n')
  const filename = `missing_unit_col_${Date.now()}.csv`
  await uploadDataset(request, csv, filename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible()

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(page.getByText(/Unit column 'user_id' is not in the uploaded data/)).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByText('Internal processing error')).not.toBeVisible()
})

// Regression for the compare-methods OOM bug itself: with Bootstrap
// explicitly added to the metric's method selection (item 3, consolidated
// package — replaces the old "Compare alternative methods" checkbox),
// Bootstrap (the method that used to crash the process at scale) runs as
// one of the comparison methods and must complete and render normally at
// ordinary data sizes.
test('Selecting Bootstrap as an extra method completes and shows it in the detailed results', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `analyze_compare_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const methodSelect = page.getByRole('combobox', { name: 'method-select-revenue' })
  await methodSelect.click()
  await expect(page.locator('.ant-select-item-option-content').first()).toBeVisible()
  await clickSelectOption(page, 'Bootstrap (bca)')
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByText(/Bootstrap/).first()).toBeVisible()
  // No row failed silently-crashed the whole table render.
  await expect(page.getByText('Failed to get job status')).not.toBeVisible()
})
