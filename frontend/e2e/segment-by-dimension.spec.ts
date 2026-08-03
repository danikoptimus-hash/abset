import { test, expect } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

// Per-dimension segment analysis + the segment-set-from-the-request bugfix:
// with 2+ declared strata, the Results tab's "Segment by" selector switches
// between each declared dimension ALONE (gender, country). It must NOT
// fabricate their "gender × country" cross — the segment set comes from the
// REQUEST, and this default demo run requested no combination (the raw gender/
// country columns aren't even in the post-period demo data — they live only in
// the stratum — so the cross isn't requestable here either).
test('Results "Segment by" selector switches between declared per-dimension segments, no auto-cross', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const n = 2000
  const rows = Array.from({ length: n }, (_, i) => {
    const gender = i % 2 === 0 ? 'M' : 'F'
    const country = i % 4 < 2 ? 'RU' : 'KZ'
    return `u${i},${100 + (i % 10)},${gender},${country}`
  })
  const csv = 'user_id,revenue,gender,country\n' + rows.join('\n')
  const filename = `segment_dim_fixture_${Date.now()}.csv`
  await uploadDataset(request, csv, filename)

  await loginViaUi(page)
  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  const datasetSelect = page.getByRole('combobox', { name: 'design-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(/Data loaded: 2000 rows/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  const expName = `segment_dim_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.locator('.ant-select', { hasText: 'Unit column' }).click()
  await page.getByTitle('user_id', { exact: true }).click()

  // Keyboard-driven (not a mouse click on the option) — same reasoning as
  // other specs picking a Dataframe-column option: a real click here is
  // consistently flaky/ambiguous (AntD Select renders more than one
  // title="revenue" node at once). Columns are [user_id, revenue, gender,
  // country]; the dropdown pre-highlights the first ("user_id"), so one
  // ArrowDown reaches "revenue".
  await page.locator('.ant-select', { hasText: 'Dataframe column' }).click()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(page.locator('.ant-select', { hasText: 'revenue' })).toBeVisible()

  await page.getByRole('button', { name: 'Next' }).click()

  // Step 3: both strata columns, isolation off, calculate + submit.
  await page.locator('.ant-select', { hasText: 'Strata (optional)' }).click()
  await page.getByTitle('gender', { exact: true }).click()
  await page.getByTitle('country', { exact: true }).click()
  await page.keyboard.press('Escape')

  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Calculate sample size' }).click()
  await expect(page.getByText(/Required per group:|No MDE target set/)).toBeVisible({ timeout: 15_000 })

  await page.getByRole('button', { name: 'Next' }).click()
  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  // Analyze with demo post-period data, then check the Results tab.
  await page.getByRole('tab', { name: 'Analysis' }).click()
  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await expect(page.getByText('Segment by:')).toBeVisible()
  const segmentedControl = page.locator('.ant-segmented')
  await expect(segmentedControl.getByText('gender', { exact: true })).toBeVisible()
  await expect(segmentedControl.getByText('country', { exact: true })).toBeVisible()
  // The fabricated all-strata cross must NOT be offered as a segment cut (it
  // was never requested). Scoped to the "Segment by" toggle — the unchanged
  // design-time strata-power table legitimately says "gender × country".
  await expect(segmentedControl.getByText('gender × country', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/By gender × country: .* vs treatment/)).toHaveCount(0)

  // Defaults to the first dimension ("gender") — its forest plot heading
  // is visible; switching to "country" changes the heading accordingly.
  await expect(page.getByText(/By gender: .* vs treatment/)).toBeVisible()
  await segmentedControl.getByText('country', { exact: true }).click()
  await expect(page.getByText(/By country: .* vs treatment/)).toBeVisible()
  await expect(page.getByText(/By gender: .* vs treatment/)).not.toBeVisible()
})
