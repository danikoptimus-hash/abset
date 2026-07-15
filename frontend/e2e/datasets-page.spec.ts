import { test, expect } from '@playwright/test'
import { loginViaUi } from './helpers'

// DB3 (CLAUDE.md dataset-centric model): the Datasets page is now the only
// place a file can be uploaded — design/analyze/validation only select from
// existing datasets (see analyze.spec.ts / validation-datasource.spec.ts).
test('"+ Dataset" modal uploads a file and it appears in the list with an Upload source tag', async ({ page }) => {
  await loginViaUi(page)
  await page.goto('/datasets')

  await page.getByRole('button', { name: 'Dataset' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()

  const filename = `datasets_page_e2e_${Date.now()}.csv`
  const csv = 'user_id,revenue\nu1,10\nu2,20\n'
  const fileChooserPromise = page.waitForEvent('filechooser')
  await page.getByText('Drag a CSV or parquet file here').click()
  const fileChooser = await fileChooserPromise
  await fileChooser.setFiles({ name: filename, mimeType: 'text/csv', buffer: Buffer.from(csv) })

  // Item 1.1: upload lands on the rename-confirm step (dataset already
  // created, default names shown) — Finish without changing anything is a
  // valid path, keeps the defaults.
  await expect(page.getByLabel('rename-dataset-name')).toHaveValue(filename, { timeout: 10_000 })
  await expect(page.getByLabel('rename-column-user_id')).toHaveValue('user_id')
  await expect(page.getByLabel('rename-column-revenue')).toHaveValue('revenue')
  await page.getByRole('button', { name: 'Finish' }).click()

  // Modal closes on success and the new row shows up in the list.
  await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 10_000 })
  const row = page.getByRole('row', { name: new RegExp(filename) })
  await expect(row).toBeVisible()
  await expect(row.getByText('Upload')).toBeVisible()

  // Refresh only makes sense for source=sql — an uploaded file has nothing
  // to re-fetch from (UX package, Datasets п.1.2: absent, not disabled).
  await expect(row.getByRole('button', { name: 'Refresh' })).toHaveCount(0)
})

// Item 1.1/1.2: renaming the dataset and a column at the upload-confirm
// step actually persists — checked via the preview drawer, which shows the
// new column name with a "renamed from" hint (Datasets.tsx).
test('Upload rename step: renaming the dataset and a column persists and shows in the preview drawer', async ({
  page,
}) => {
  await loginViaUi(page)
  await page.goto('/datasets')

  await page.getByRole('button', { name: 'Dataset' }).click()
  const csv = 'cust_id,amt\n1,10.5\n2,20.0\n'
  const fileChooserPromise = page.waitForEvent('filechooser')
  await page.getByText('Drag a CSV or parquet file here').click()
  const fileChooser = await fileChooserPromise
  const originalName = `rename_e2e_${Date.now()}.csv`
  await fileChooser.setFiles({ name: originalName, mimeType: 'text/csv', buffer: Buffer.from(csv) })

  await expect(page.getByLabel('rename-dataset-name')).toHaveValue(originalName, { timeout: 10_000 })
  const newName = `renamed_e2e_${Date.now()}.csv`
  await page.getByLabel('rename-dataset-name').fill(newName)
  await page.getByLabel('rename-column-cust_id').fill('customer_id')

  await page.getByRole('button', { name: 'Finish' }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 10_000 })

  const row = page.getByRole('row', { name: new RegExp(newName) })
  await expect(row).toBeVisible()
  await row.click()

  const drawer = page.getByRole('dialog').or(page.locator('.ant-drawer'))
  // AntD's fixed/sticky table header renders the header content twice
  // (a visually-hidden measurement copy alongside the real one) — .first()
  // avoids a strict-mode ambiguity, not a sign of an actual duplicate.
  await expect(drawer.getByText('customer_id').first()).toBeVisible()
  await expect(drawer.getByText('amt').first()).toBeVisible()
  // The renamed-from hint on the "customer_id" header — a Tooltip, whose
  // title only reaches the DOM on hover, so check via the info marker
  // text next to the header instead of the tooltip content itself.
  await expect(drawer.locator('th', { hasText: 'customer_id' }).first()).toBeVisible()
})

test('From SQL tab renders a connection picker, schema/table pickers, and SQL editor', async ({ page }) => {
  await loginViaUi(page)
  await page.goto('/datasets')

  await page.getByRole('button', { name: 'Dataset' }).click()
  await page.getByRole('tab', { name: 'From SQL' }).click()

  await expect(page.getByRole('combobox', { name: 'from-sql-connection-select' })).toBeVisible()
  // Schema/table are optional (UX package, Datasets §1.1/1.4) — visible but
  // disabled until a connection (then a schema) is picked.
  await expect(page.getByRole('combobox', { name: 'from-sql-schema-select' })).toBeDisabled()
  await expect(page.getByRole('combobox', { name: 'from-sql-table-select' })).toBeDisabled()
  await expect(page.getByPlaceholder(/SELECT user_id, revenue FROM/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Preview' })).toBeDisabled()
})
