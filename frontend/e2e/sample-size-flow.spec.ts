import { test, expect } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

// Item 3 (sample-size-first wizard flow): group NAMES are set before any
// sample-size math happens; proportions only appear AFTER "Calculate sample
// size", defaulting to an equal split, with "Minimize control group" as a
// guided shortcut and live per-group validation against the required size.
//
// Fixture: exactly 500/500 (no sampling noise) binary "converted" column,
// n=1000 — an absolute MDE of 20pp against that exact 50% baseline needs
// ~93 per group (computed offline via abkit.design.power.sample_size_binary),
// i.e. a "minimize control" split of roughly 9-10% control / 90-91%
// treatment — this is the "10/90 on a fixture where that's the result"
// case from the spec, engineered rather than coincidental.
test('sample-size-first flow: Calculate, 50/50 default, Minimize control, and a below-minimum warning', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const n = 1000
  const rows = Array.from({ length: n }, (_, i) => `u${i},${i % 2 === 0 ? 1 : 0}`)
  const csv = 'user_id,converted\n' + rows.join('\n')
  const filename = `sample_size_fixture_${Date.now()}.csv`
  await uploadDataset(request, csv, filename)

  await loginViaUi(page)
  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  // Step 1: pick the uploaded fixture (not Demo Data, which wouldn't give
  // an exact, noise-free 50% baseline to compute a clean fixture around).
  const datasetSelect = page.getByRole('combobox', { name: 'design-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(/Data loaded: 1000 rows/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 2: name, unit_col, and ONLY group names (no proportions here —
  // item 3.1a) plus the metric (type=binary, column=converted).
  const expName = `wizard_samplesize_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.locator('.ant-select', { hasText: 'Unit column' }).click()
  await page.getByTitle('user_id', { exact: true }).click()

  await expect(page.getByPlaceholder('Group name').first()).toHaveValue('control')
  await expect(page.getByPlaceholder('Group name').nth(1)).toHaveValue('treatment')
  // No proportion InputNumber on this step for the abkit-split path.
  await expect(page.getByRole('spinbutton')).toHaveCount(0)

  await page.locator('.ant-select').filter({ hasText: 'continuous' }).first().click()
  await page.getByTitle('binary', { exact: true }).click()
  // Selecting "binary" adds a "Suitable 0/1 columns" hint block below the
  // metric card (Step2GroupsMetrics.tsx), reflowing the layout right where
  // the next dropdown opens — a mouse click on its option kept resolving to
  // an element stuck "not visible"/"not stable" even after waiting for that
  // reflow to finish first. Keyboard-driven selection sidesteps the
  // problem entirely, since it never depends on the option's on-screen
  // position: columns are ['user_id', 'converted'], and AntD pre-highlights
  // the FIRST option ('user_id') as soon as the dropdown opens — one
  // ArrowDown moves to 'converted', then Enter picks it (confirmed via the
  // accessibility snapshot: a SECOND ArrowDown wrapped back to 'user_id').
  await expect(page.getByText(/Suitable 0\/1 columns/)).toBeVisible()
  await page.locator('.ant-select', { hasText: 'Dataframe column' }).click()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(page.locator('.ant-select', { hasText: 'converted' })).toBeVisible()

  await page.getByRole('button', { name: 'Next' }).click()

  // Step 3: absolute MDE (20pp) on "converted", isolation off (repeatable
  // across e2e runs against the same DB, same reasoning as other wizard
  // specs), then Calculate.
  await page.getByText('Set a target absolute MDE').click()
  await page.locator('.ant-select', { hasText: 'Metric to set the absolute MDE for' }).click()
  // Keyboard-driven, same reasoning as the Step2 dataframe-column select
  // above — a mouse click on this dropdown's option was consistently stuck
  // "not visible"/"not stable" here too. Only one metric exists ("converted"),
  // pre-highlighted as soon as the dropdown opens, so Enter alone confirms it.
  await page.keyboard.press('Enter')
  await expect(page.locator('.ant-select', { hasText: 'converted' }).last()).toBeVisible()
  const mdeInput = page.getByRole('spinbutton').first()
  await mdeInput.fill('20')

  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()

  // No proportions block until Calculate has run at least once.
  await expect(page.getByText('Group Proportions')).not.toBeVisible()

  await page.getByRole('button', { name: 'Calculate sample size' }).click()
  await expect(page.getByText(/Required per group:/)).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('Your dataset:', { exact: false })).toContainText('1000 eligible users')

  // Default: equal (50/50) split, item 3.1d.
  const controlShare = page.getByRole('spinbutton', { name: 'group-share-control' })
  const treatmentShare = page.getByRole('spinbutton', { name: 'group-share-treatment' })
  await expect(controlShare).toHaveValue('0.50')
  await expect(treatmentShare).toHaveValue('0.50')

  // Minimize control group — control gets ~required/eligible (~9-10%), the
  // rest goes to treatment (item 3.1e).
  await page.getByRole('button', { name: 'Minimize control group' }).click()
  const controlValueAfterMinimize = Number(await controlShare.inputValue())
  expect(controlValueAfterMinimize).toBeGreaterThan(0.05)
  expect(controlValueAfterMinimize).toBeLessThan(0.15)
  const treatmentValueAfterMinimize = Number(await treatmentShare.inputValue())
  expect(controlValueAfterMinimize + treatmentValueAfterMinimize).toBeCloseTo(1, 2)

  // Manually setting a share below the required minimum shows an inline
  // per-group warning (item 3.1f).
  await controlShare.click()
  await controlShare.press('Control+A')
  await controlShare.pressSequentially('0.01')
  await controlShare.press('Tab')
  await expect(page.getByText(/would get \d+ < required \d+ users — power will be below target/)).toBeVisible()

  // Recalculating after changing the MDE marks the old result stale
  // (item 3.2) without discarding the proportions the user already entered.
  await mdeInput.click()
  await mdeInput.press('Control+A')
  await mdeInput.pressSequentially('10')
  await mdeInput.press('Tab')
  await expect(page.getByText(/Inputs changed since this was calculated/)).toBeVisible()
  await expect(controlShare).toHaveValue('0.01') // untouched by the staleness itself

  // The rest of the flow (submit) still works end to end.
  await page.getByRole('button', { name: 'Calculate sample size' }).click()
  await expect(page.getByText(/Inputs changed since this was calculated/)).not.toBeVisible()
  // Restore a valid split before proceeding — the below-minimum share
  // above is still allowed to submit (a warning, not a hard block), but a
  // valid sum is required to leave this step.
  await controlShare.click()
  await controlShare.press('Control+A')
  await controlShare.pressSequentially('0.5')
  await controlShare.press('Tab')
  await treatmentShare.click()
  await treatmentShare.press('Control+A')
  await treatmentShare.pressSequentially('0.5')
  await treatmentShare.press('Tab')

  await page.getByRole('button', { name: 'Next' }).click()
  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })
  await expect(page.getByText('MDE Table')).toBeVisible()
})
