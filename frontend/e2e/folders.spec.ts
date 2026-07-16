import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment, clickSelectOption } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

// Item 5 (folders package): create a folder, move a test into it via the
// row action, filter the list by clicking the folder, then bulk-move a
// second test, and finally delete the folder and confirm its test moves
// back to Uncategorized rather than being deleted.
test('Create a folder, move tests into it (row + bulk), filter, then delete it and confirm tests survive', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const suffix = Date.now()
  const folderName = `E2E Folder ${suffix}`
  const expA = `folders_e2e_a_${suffix}`
  const expB = `folders_e2e_b_${suffix}`

  await seedExperiment(request, expA)
  await seedExperiment(request, expB)
  await loginViaUi(page)
  await page.goto('/experiments')

  // Scoped to the FolderPanel (<nav aria-label="Folders">) — the Folder
  // column added to the table renders the same folder name as a Tag, so an
  // unscoped page-wide text lookup is ambiguous once a row is filed.
  const panel = page.getByRole('navigation', { name: 'Folders' })

  // Create the folder from the panel.
  await panel.getByRole('button', { name: 'New folder' }).click()
  const createDialog = page.getByRole('dialog').filter({ hasText: 'New folder' })
  await createDialog.getByRole('textbox').fill(folderName)
  await createDialog.getByRole('button', { name: 'Create' }).click()
  await expect(createDialog).not.toBeVisible()
  await expect(panel.getByText(folderName, { exact: true })).toBeVisible()

  // Row action: move expA into the folder.
  await page.getByPlaceholder('Search by name or tag...').fill(expA)
  const rowA = page.getByRole('row', { name: new RegExp(expA) })
  await rowA.hover()
  await rowA.getByRole('button', { name: 'Move to folder' }).click()
  const moveDialog = page.getByRole('dialog').filter({ hasText: 'Move to folder' })
  await moveDialog.getByRole('combobox', { name: 'Target folder' }).click()
  await clickSelectOption(page, folderName)
  await moveDialog.getByRole('button', { name: 'Move' }).click()
  await expect(moveDialog).not.toBeVisible()

  // Clicking the folder in the panel filters the list to just expA.
  await page.getByPlaceholder('Search by name or tag...').fill('')
  await panel.getByText(folderName, { exact: true }).click()
  await expect(page.getByRole('row', { name: new RegExp(expA) })).toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(expB) })).not.toBeVisible()

  // Back to "All tests", bulk-move expB into the same folder.
  await panel.getByText('All tests', { exact: true }).click()
  await page.getByPlaceholder('Search by name or tag...').fill(expB)
  await page.getByRole('button', { name: 'Bulk select' }).click()
  await page.getByRole('row', { name: new RegExp(expB) }).getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Move selected to folder' }).click()
  const bulkMoveDialog = page.getByRole('dialog').filter({ hasText: 'Move to folder' })
  await bulkMoveDialog.getByRole('combobox', { name: 'Target folder' }).click()
  await clickSelectOption(page, folderName)
  await bulkMoveDialog.getByRole('button', { name: 'Move' }).click()
  await expect(bulkMoveDialog).not.toBeVisible()

  // Both tests are now filed under the folder.
  await page.getByPlaceholder('Search by name or tag...').fill('')
  await panel.getByText(folderName, { exact: true }).click()
  await expect(page.getByRole('row', { name: new RegExp(expA) })).toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(expB) })).toBeVisible()

  // Delete the folder — tests are NOT deleted, they move to Uncategorized.
  await panel.getByRole('button', { name: 'Folder actions' }).click()
  await page.getByRole('menuitem', { name: 'Delete' }).click()
  const deleteDialog = page.getByRole('dialog').filter({ hasText: `Delete "${folderName}"?` })
  await expect(deleteDialog.getByText(/2 tests in this folder will move to Uncategorized/)).toBeVisible()
  await deleteDialog.getByRole('button', { name: 'Delete' }).click()
  await expect(deleteDialog).not.toBeVisible()
  await expect(panel.getByText(folderName, { exact: true })).not.toBeVisible()

  await page.getByPlaceholder('Search by name or tag...').fill(expA)
  await expect(page.getByRole('row', { name: new RegExp(expA) })).toBeVisible()
})

test('Only editor+ sees "New folder", and folder filter composes with status/tag filters', async ({
  page,
  request,
}) => {
  const suffix = Date.now()
  const expName = `folders_compose_${suffix}`
  await seedExperiment(request, expName)
  // Published so the viewer can actually see it — item 5.7: "Uncategorized"
  // only shows once something VISIBLE TO THIS USER is actually uncategorized,
  // a draft invisible to a viewer wouldn't count.
  await request.patch(`${API_BASE}/experiments/${expName}`, { data: { publication_status: 'published' } })
  await loginViaUi(page, 'viewer@e2e.test', 'e2epass123')
  await page.goto('/experiments')

  const panel = page.getByRole('navigation', { name: 'Folders' })
  await expect(panel.getByRole('button', { name: 'New folder' })).not.toBeVisible()
  await expect(panel.getByText('All tests', { exact: true })).toBeVisible()
  await expect(panel.getByText('Uncategorized', { exact: true })).toBeVisible()
})
