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

test('header shows "Last modified by" instead of an owner avatar', async ({ page, request }) => {
  const name = `e2e_lastmod_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  // Design already audits "experiment.create" — the header has something to
  // show right after seeding, no further edit needed (UX package, п.4).
  await expect(page.getByText(/Last modified by E2E Admin/)).toBeVisible()
  await expect(page.locator('.ant-avatar')).toHaveCount(0)
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

// 6-part package pt.8: forward transitions stay frictionless; backward ones
// (here: running -> designed, then completed -> running) show a confirm
// modal with a transition-specific warning before applying, and canceling
// leaves the status untouched.
test('backward status transitions show a confirm modal with transition-specific warnings', async ({
  page,
  request,
}) => {
  const name = `e2e_statusbackward_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  await page.getByText('designed', { exact: true }).click()
  await page.getByText('Move to running').click()
  await expect(page.getByText('running', { exact: true })).toBeVisible()

  // running -> designed: backward, confirm modal with the Redesign hint.
  await page.getByText('running', { exact: true }).click()
  await page.getByText('Move to designed').click()
  const toDesignedDialog = page.getByRole('dialog').filter({ hasText: "Move to 'designed'?" })
  await expect(toDesignedDialog).toBeVisible()
  await expect(toDesignedDialog.getByText(/Existing analyses will be KEPT/)).toBeVisible()
  await toDesignedDialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(toDesignedDialog).not.toBeVisible()
  await expect(page.getByText('running', { exact: true })).toBeVisible() // canceled — unchanged

  await page.getByText('running', { exact: true }).click()
  await page.getByText('Move to designed').click()
  await expect(toDesignedDialog).toBeVisible()
  await toDesignedDialog.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByText('designed', { exact: true })).toBeVisible()

  // designed -> running -> completed -> running: the last hop is backward
  // (peeking warning).
  await page.getByText('designed', { exact: true }).click()
  await page.getByText('Move to running').click()
  await expect(page.getByText('running', { exact: true })).toBeVisible()
  await page.getByText('running', { exact: true }).click()
  await page.getByText('Move to completed').click()
  await expect(page.getByText('completed', { exact: true })).toBeVisible()

  await page.getByText('completed', { exact: true }).click()
  await page.getByText('Move to running').click()
  const reopenDialog = page.getByRole('dialog').filter({ hasText: "Move to 'running'?" })
  await expect(reopenDialog).toBeVisible()
  await expect(reopenDialog.getByText(/inflates false positive rates \(peeking\)/)).toBeVisible()
  await reopenDialog.getByRole('button', { name: 'Continue' }).click()
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
  await expect(page.getByText('Analysis options')).toBeVisible()

  await page.reload()
  await expect(page.getByRole('tab', { name: 'Analysis', selected: true })).toBeVisible()
  await expect(page.getByText('Analysis options')).toBeVisible()
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

// Stage 2 (lifecycle dates), items 2.1/2.5: only present dates show, in
// order, and completed_at resets when reopening a completed test (backward
// completed->running) — the header line must reflect that live, not just
// the DB column tested separately in backend/tests/test_experiments_mutations.py.
test('header shows only present lifecycle dates, and reopening a completed test drops "Completed"', async ({
  page,
  request,
}) => {
  const name = `e2e_lifecycle_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  // designed: only "Created", never "Started"/"Completed".
  await expect(page.getByText(/Created \w+ \d+/)).toBeVisible()
  await expect(page.getByText(/Started \w+ \d+/)).not.toBeVisible()
  await expect(page.getByText(/Completed \w+ \d+/)).not.toBeVisible()

  await page.getByText('designed', { exact: true }).click()
  await page.getByText('Move to running').click()
  await expect(page.getByText('running', { exact: true })).toBeVisible()
  await expect(page.getByText(/Created \w+ \d+ · Started \w+ \d+/)).toBeVisible()
  await expect(page.getByText(/Completed \w+ \d+/)).not.toBeVisible()

  await page.getByText('running', { exact: true }).click()
  await page.getByText('Move to completed').click()
  await expect(page.getByText('completed', { exact: true })).toBeVisible()
  await expect(page.getByText(/Created \w+ \d+ · Started \w+ \d+ · Completed \w+ \d+/)).toBeVisible()

  // completed -> running (reopen): "Completed" must disappear again.
  await page.getByText('completed', { exact: true }).click()
  await page.getByText('Move to running').click()
  const reopenDialog = page.getByRole('dialog').filter({ hasText: "Move to 'running'?" })
  await expect(reopenDialog).toBeVisible()
  await reopenDialog.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByText('running', { exact: true })).toBeVisible()
  await expect(page.getByText(/Created \w+ \d+ · Started \w+ \d+/)).toBeVisible()
  await expect(page.getByText(/Completed \w+ \d+/)).not.toBeVisible()
})
