import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

// UX package (Superset-style list): owner avatar, relative time, hover-reveal
// actions, and the Edit Properties modal.
test('owner avatar tooltip, relative last-modified time, and hover-reveal actions', async ({
  page,
  request,
}) => {
  const name = `e2e_listux_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  const row = page.getByRole('row', { name: new RegExp(name) })
  await expect(row).toBeVisible()

  // Owner avatar shows initials and a full-name/email tooltip on hover.
  await row.locator('.ant-avatar').first().hover()
  await expect(page.getByRole('tooltip')).toContainText('@')

  // Last Modified shows a relative time (freshly seeded, so "... ago").
  await expect(row.getByText(/ago|few seconds/)).toBeVisible()

  // Actions are hover-reveal: hovering the row makes Edit/Delete clickable.
  await row.hover()
  await expect(row.getByRole('button', { name: 'Edit' })).toBeVisible()
  await expect(row.getByRole('button', { name: 'Delete' })).toBeVisible()
})

test('Edit Properties modal renames the experiment from the list', async ({ page, request }) => {
  const name = `e2e_props_${Date.now()}`
  const newName = `${name}_renamed`
  await seedExperiment(request, name)
  await loginViaUi(page)

  const row = page.getByRole('row', { name: new RegExp(name) })
  await row.hover()
  await row.getByRole('button', { name: 'Edit' }).click()

  const modal = page.getByRole('dialog')
  await expect(modal.getByText('Edit Properties')).toBeVisible()
  await modal.getByLabel('Name').fill(newName)
  await modal.getByRole('button', { name: 'Save' }).click()

  // exact: true matters here — the old name is a prefix of newName, so a
  // substring match would (wrongly) still match the renamed row's link too.
  await expect(page.getByRole('link', { name, exact: true })).not.toBeVisible()
  await expect(page.getByRole('link', { name: newName, exact: true })).toBeVisible()
})

test('Edit Properties modal is also reachable from the "..." menu on the experiment page', async ({
  page,
  request,
}) => {
  const name = `e2e_props_page_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByText('Edit Properties').click()

  const modal = page.getByRole('dialog')
  await expect(modal.getByText('Edit Properties')).toBeVisible()
  await modal.getByRole('button', { name: 'Cancel' }).click()
})
