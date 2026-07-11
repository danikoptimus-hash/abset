import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment, uploadDataset } from './helpers'

// Regression for the compare-methods crash bug: a failed analyze job must
// show its real, human-readable error (job.error) instead of the generic
// "Failed to get job status" that used to appear whenever the backend
// process died mid-poll (OOM-killed worker). Triggering a genuine OOM here
// isn't practical in e2e — instead we trigger a deterministic, real
// AnalysisError (duplicate unit_id in the uploaded data) and check the UI
// surfaces its actual message.
test('a failed analyze job shows its real error message, not a generic one', async ({
  page,
  request,
}) => {
  const name = `analyze_fail_e2e_${Date.now()}`
  await seedExperiment(request, name)

  // Every row uses the same user_id -> check_no_duplicates raises
  // AnalysisError before any join happens. Uploaded via the API BEFORE
  // navigating: DatasetSelect's query is fetched once on mount and isn't
  // invalidated by an out-of-band API call happening after the page loads.
  const csv = 'user_id,revenue\n' + Array.from({ length: 50 }, () => `dup_user,${100}`).join('\n')
  const dupesFilename = `dupes_${Date.now()}.csv`
  await uploadDataset(request, csv, dupesFilename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(dupesFilename)
  await page.getByTitle(dupesFilename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${dupesFilename.replace('.', '\\.')}`))).toBeVisible()

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(page.getByText(/duplicate 'user_id' values/)).toBeVisible({ timeout: 20_000 })
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

// Regression for the compare-methods OOM bug itself: with the checkbox
// checked, Bootstrap (the method that used to crash the process at scale)
// runs as one of the alternative methods and must complete and render
// normally at ordinary data sizes.
test('Compare alternative methods completes and shows Bootstrap in the detailed results', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `analyze_compare_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  // Compare alternative methods is checked by default (5-part package
  // pt.4) — no need to open Advanced options and check it manually.
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
