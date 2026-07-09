import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

test('delete button is disabled until exact "DELETE" is typed, then removes the row', async ({
  page,
  request,
}) => {
  const name = `e2e_delete_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  const row = page.getByRole('row', { name: new RegExp(name) })
  await row.hover()
  await row.getByRole('button', { name: 'Delete' }).click()

  const modal = page.getByRole('dialog')
  const okButton = modal.getByRole('button', { name: 'Delete' })
  await expect(okButton).toBeDisabled()

  await modal.getByRole('textbox').fill('delete')
  await expect(okButton).toBeDisabled()

  await modal.getByRole('textbox').fill('DELETE')
  await expect(okButton).toBeEnabled()
  await okButton.click()

  await expect(page.getByRole('link', { name })).not.toBeVisible()
})
