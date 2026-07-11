import { test, expect } from '@playwright/test'
import { loginViaUi } from './helpers'

// FRONTEND.md §7 R5: "Playwright: e2e создание теста на демо-данных ->
// страница теста -> publish -> edit блока «Гипотеза»."
test('create experiment via wizard on demo data, then publish and edit hypothesis', async ({ page }) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  // Step 1: demo data
  await page.getByRole('button', { name: 'Demo Data' }).click()
  await expect(page.getByText(/Data loaded: 5000 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 2: experiment name (demo data already pre-fills groups/metrics)
  const expName = `wizard_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 3: parameters — isolation=off (robust to repeated e2e runs against
  // the same DB: demo data is deterministic, seed=0 -> the same 5000 users
  // every time; the default "exclude" would exclude users already occupied
  // by experiments from previous runs, leaving 0 candidates).
  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 4: run
  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  // Experiment page: lands on the Design tab, configuration and MDE table visible
  await expect(page.getByRole('tab', { name: 'Design', selected: true })).toBeVisible()
  await expect(page.getByText('MDE Table')).toBeVisible()

  // Publish — click the Draft/Published status badge itself (UX package,
  // section 1.1: it's both indicator and toggle, no separate button anymore)
  const draftBadge = page.getByText('draft', { exact: true })
  await expect(draftBadge).toBeVisible()
  await draftBadge.click()
  await expect(page.getByText('published', { exact: true })).toBeVisible()

  // Edit -> change the "Hypothesis" block -> Save
  await page.getByRole('button', { name: 'Edit' }).click()
  const hypothesisTextarea = page.locator('textarea').first()
  await hypothesisTextarea.fill('New hypothesis from the e2e test')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Saved')).toBeVisible()
  // Wait for edit mode to fully tear down (textarea unmounted) before
  // checking the read-only render — otherwise there's a brief window where
  // both exist and a plain getByText match is ambiguous (strict mode).
  await expect(page.locator('textarea')).toHaveCount(0)
  await expect(page.getByText('New hypothesis from the e2e test')).toBeVisible()
})

// 5-item follow-up п.14: the wizard's optional Hypothesis field (step 2,
// below the name field) saves into the experiment's existing Hypothesis
// block on design — visible immediately on the experiment page, no manual
// edit needed.
test('hypothesis entered in the wizard is saved into the experiment\'s Hypothesis block', async ({ page }) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await page.getByRole('button', { name: 'Demo Data' }).click()
  await expect(page.getByText(/Data loaded: 5000 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  const expName = `wizard_hypothesis_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.getByLabel('Hypothesis').fill('If we change the checkout button color, conversion will increase.')
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  await expect(
    page.getByText('If we change the checkout button color, conversion will increase.'),
  ).toBeVisible()
})
