import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

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
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  // Every row uses the same user_id -> check_no_duplicates raises
  // AnalysisError before any join happens.
  const csv = 'user_id,revenue\n' + Array.from({ length: 50 }, () => `dup_user,${100}`).join('\n')
  const fileChooserPromise = page.waitForEvent('filechooser')
  await page.getByText('Upload post-period data (CSV)').click()
  const fileChooser = await fileChooserPromise
  await fileChooser.setFiles({ name: 'dupes.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) })
  await expect(page.getByText(/Data ready: dupes\.csv/)).toBeVisible()

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(page.getByText(/duplicate 'user_id' values/)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Failed to get job status')).not.toBeVisible()
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

  await page.getByRole('checkbox', { name: 'Compare alternative methods' }).check()
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
