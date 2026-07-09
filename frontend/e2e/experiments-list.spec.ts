import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

test('experiments list shows a seeded experiment and search filters it', async ({ page, request }) => {
  const name = `e2e_list_${Date.now()}`
  await seedExperiment(request, name)

  await loginViaUi(page)
  await expect(page.getByRole('link', { name })).toBeVisible()

  await page.getByPlaceholder('Search by name').fill('does-not-exist-xyz')
  await page.getByPlaceholder('Search by name').press('Enter')
  await expect(page.getByRole('link', { name })).not.toBeVisible()

  await page.getByPlaceholder('Search by name').fill(name)
  await page.getByPlaceholder('Search by name').press('Enter')
  await expect(page.getByRole('link', { name })).toBeVisible()
})

test('clicking an experiment name opens its detail page', async ({ page, request }) => {
  const name = `e2e_detail_${Date.now()}`
  await seedExperiment(request, name)

  await loginViaUi(page)
  await page.getByRole('link', { name }).click()

  await expect(page).toHaveURL(new RegExp(`/experiments/${name}$`))
  await expect(page.getByText('Configuration')).toBeVisible()
})
