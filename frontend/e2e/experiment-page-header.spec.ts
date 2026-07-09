import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

// UX package (Superset-style experiment page header): clickable status
// badges, tabs bound to the URL, and the top-right Edit button.
const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

test('clicking the Draft/Published badge toggles publication status', async ({ page, request }) => {
  const name = `e2e_pubbadge_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  const draftBadge = page.getByText('draft', { exact: true })
  await expect(draftBadge).toBeVisible()
  await draftBadge.click()
  await expect(page.getByText('published', { exact: true })).toBeVisible()

  await page.getByText('published', { exact: true }).click()
  await expect(page.getByText('draft', { exact: true })).toBeVisible()
})

test('status badge dropdown transitions the operational status', async ({ page, request }) => {
  const name = `e2e_statusbadge_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  await expect(page.getByText('designed', { exact: true })).toBeVisible()
  await page.getByText('designed', { exact: true }).click()
  await page.getByText('Move to running').click()
  await expect(page.getByText('running', { exact: true })).toBeVisible()
})

test('tabs switch and persist in the URL across a reload', async ({ page, request }) => {
  const name = `e2e_tabs_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  await expect(page.getByRole('tab', { name: 'Design', selected: true })).toBeVisible()

  await page.getByRole('tab', { name: 'Analysis' }).click()
  await expect(page).toHaveURL(/\?tab=analysis/)
  await expect(page.getByText('Upload post-period data (CSV)')).toBeVisible()

  await page.reload()
  await expect(page.getByRole('tab', { name: 'Analysis', selected: true })).toBeVisible()
  await expect(page.getByText('Upload post-period data (CSV)')).toBeVisible()
})

test('Edit button top-right opens edit mode', async ({ page, request }) => {
  const name = `e2e_editbtn_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  await page.getByRole('button', { name: 'Edit' }).click()
  await expect(page.getByRole('button', { name: 'Save' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Discard' })).toBeVisible()
  await expect(page.locator('textarea').first()).toBeVisible()
})

test('viewer sees status badges but cannot click them', async ({ page, request }) => {
  const name = `e2e_viewerbadge_${Date.now()}`
  await seedExperiment(request, name)
  const patchResp = await request.patch(`${API_BASE}/experiments/${name}`, {
    data: { publication_status: 'published' },
  })
  expect(patchResp.ok()).toBeTruthy()

  await loginViaUi(page, 'viewer@e2e.test', 'e2epass123')
  await page.goto(`/experiments/${name}`)

  await expect(page.getByText('published', { exact: true })).toBeVisible()
  await expect(page.getByText('designed', { exact: true })).toBeVisible()

  // Neither badge opens a status-transition menu for a viewer — clicking is
  // a no-op, so status/publication must be unchanged afterwards.
  await page.getByText('published', { exact: true }).click()
  await page.getByText('designed', { exact: true }).click()

  await expect(page.getByText('Move to running')).not.toBeVisible()
  await expect(page.getByText('published', { exact: true })).toBeVisible()
  await expect(page.getByText('designed', { exact: true })).toBeVisible()
})
