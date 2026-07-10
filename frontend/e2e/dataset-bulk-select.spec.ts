import { test, expect } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

// Datasets bulk select/delete — mirrors experiments-bulk-select.spec.ts's
// pattern (same toggle -> checkboxes -> action bar -> typed-DELETE shape),
// adapted for dataset rows.

test('bulk select: toggling the mode shows checkboxes, selecting rows shows the action bar', async ({
  page,
  request,
}) => {
  const nameA = `bulk_ds_toggle_a_${Date.now()}.csv`
  const nameB = `bulk_ds_toggle_b_${Date.now()}.csv`
  await uploadDataset(request, 'a,b\n1,2\n', nameA)
  await uploadDataset(request, 'a,b\n1,2\n', nameB)
  await loginViaUi(page)
  await page.goto('/datasets')

  await expect(page.locator('.ant-checkbox')).toHaveCount(0)
  await page.getByRole('button', { name: 'Bulk select' }).click()
  await expect(page.locator('.ant-checkbox').first()).toBeVisible()

  await page.getByRole('row', { name: new RegExp(nameA) }).getByRole('checkbox').check()
  await page.getByRole('row', { name: new RegExp(nameB) }).getByRole('checkbox').check()
  await expect(page.getByText('2 selected')).toBeVisible()

  await page.getByRole('button', { name: 'Deselect all' }).click()
  await expect(page.getByText('2 selected')).not.toBeVisible()
  await expect(page.locator('.ant-checkbox')).toHaveCount(0)
})

test('bulk delete removes three selected datasets after typing DELETE', async ({ page, request }) => {
  const nameA = `bulk_ds_a_${Date.now()}.csv`
  const nameB = `bulk_ds_b_${Date.now()}.csv`
  const nameC = `bulk_ds_c_${Date.now()}.csv`
  await uploadDataset(request, 'a,b\n1,2\n', nameA)
  await uploadDataset(request, 'a,b\n1,2\n', nameB)
  await uploadDataset(request, 'a,b\n1,2\n', nameC)
  await loginViaUi(page)
  await page.goto('/datasets')

  await page.getByRole('button', { name: 'Bulk select' }).click()
  await page.getByRole('row', { name: new RegExp(nameA) }).getByRole('checkbox').check()
  await page.getByRole('row', { name: new RegExp(nameB) }).getByRole('checkbox').check()
  await page.getByRole('row', { name: new RegExp(nameC) }).getByRole('checkbox').check()
  await expect(page.getByText('3 selected')).toBeVisible()

  await page.getByRole('button', { name: 'Delete selected' }).click()
  const modal = page.getByRole('dialog')
  await expect(modal.getByText(nameA)).toBeVisible()
  await expect(modal.getByText(nameB)).toBeVisible()
  await expect(modal.getByText(nameC)).toBeVisible()

  const okButton = modal.getByRole('button', { name: 'Delete' })
  await expect(okButton).toBeDisabled()
  await modal.getByRole('textbox').fill('DELETE')
  await expect(okButton).toBeEnabled()
  await okButton.click()

  await expect(page.getByText('Deleted 3 datasets')).toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(nameA) })).not.toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(nameB) })).not.toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(nameC) })).not.toBeVisible()
})

test('bulk delete skips a dataset without permission and reports it', async ({ page, request }) => {
  const ownName = `bulk_ds_own_${Date.now()}.csv`
  const othersName = `bulk_ds_others_${Date.now()}.csv`
  const editorEmail = `bulk_ds_editor_${Date.now()}@e2e.test`
  const editorPassword = 'e2epass123'

  // Someone else's dataset — uploaded by the default admin fixture, so a
  // different (non-admin) editor can see it in the list but not delete it.
  await uploadDataset(request, 'a,b\n1,2\n', othersName)

  const createResp = await request.post(`${API_BASE}/admin/users`, {
    data: { email: editorEmail, first_name: 'Bulk', last_name: 'Editor', role: 'editor', password: editorPassword },
  })
  expect(createResp.ok()).toBeTruthy()
  await uploadDataset(request, 'a,b\n1,2\n', ownName, { email: editorEmail, password: editorPassword })

  await loginViaUi(page, editorEmail, editorPassword)
  await page.goto('/datasets')

  await page.getByRole('button', { name: 'Bulk select' }).click()
  await page.getByRole('row', { name: new RegExp(ownName) }).getByRole('checkbox').check()
  await page.getByRole('row', { name: new RegExp(othersName) }).getByRole('checkbox').check()
  await expect(page.getByText('2 selected')).toBeVisible()

  await page.getByRole('button', { name: 'Delete selected' }).click()
  const modal = page.getByRole('dialog')
  await modal.getByRole('textbox').fill('DELETE')
  await modal.getByRole('button', { name: 'Delete' }).click()

  await expect(page.getByText(new RegExp(`Deleted 1, skipped 1.*${othersName}`))).toBeVisible()

  await page.getByRole('button', { name: 'OK' }).click()
  await expect(page.getByRole('row', { name: new RegExp(ownName) })).not.toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(othersName) })).toBeVisible()
})
