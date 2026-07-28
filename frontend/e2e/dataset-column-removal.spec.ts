import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment, uploadDataset } from './helpers'

// Part 2 (removable columns), scenario (a): exclude a column at CREATION (on
// the upload confirm step) and verify no picker/preview offers it afterwards.
test('exclude a column on the upload confirm step; pickers no longer offer it', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `col_removal_create_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  // Create a dataset from CSV with a spurious 'group' column, drop it on the
  // confirm step, Finish.
  await page.goto('/datasets')
  await page.getByRole('button', { name: 'Dataset' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()

  const filename = `results_${Date.now()}.csv`
  const csv = 'user_id,revenue,group\nu1,10,A\nu2,20,B\nu3,30,A\n'
  const fileChooserPromise = page.waitForEvent('filechooser')
  await page.getByText('Drag a CSV or parquet file here').click()
  ;(await fileChooserPromise).setFiles({ name: filename, mimeType: 'text/csv', buffer: Buffer.from(csv) })

  await expect(page.getByLabel('rename-dataset-name')).toHaveValue(filename, { timeout: 10_000 })
  // Remove 'group' — it moves to the "Removed columns" section (restore shows).
  await page.getByLabel('remove-column-group').click()
  await expect(page.getByLabel('restore-column-group')).toBeVisible()
  await page.getByRole('button', { name: 'Finish' }).click()
  await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 10_000 })

  // The picker in Analyze must not offer 'group'. Selecting the dataset shows
  // "Data ready" (no reserved-column collision, since 'group' is excluded from
  // the visible columns), and the Date-column select lists revenue but not
  // 'group'.
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()
  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(/Data ready:/)).toBeVisible()
  await expect(page.getByText(/reserved column/)).toHaveCount(0)

  // "Data ready: ... 2 columns" above already proves 'group' is gone from the
  // visible set; confirm the Date-column picker specifically offers no 'group'
  // option (but does offer 'revenue').
  await page.getByRole('combobox', { name: 'date-column-select' }).click()
  await expect(page.getByRole('option', { name: 'revenue', exact: true })).toHaveCount(1)
  await expect(page.getByRole('option', { name: 'group', exact: true })).toHaveCount(0)
})

// Part 2 scenario (b) + Part 1 recovery: a dataset with a reserved 'group'
// column blocks analysis; removing the column via Edit and returning to the
// Analysis tab lets the run succeed — the full recovery path end to end.
test('remove a reserved column via Edit, then re-run analysis successfully', async ({ page, request }) => {
  test.setTimeout(90_000)
  const name = `col_removal_recover_${Date.now()}`
  await seedExperiment(request, name)

  // Post-period dataset with matching user ids AND a reserved 'group' column.
  const badCsv = ['user_id,revenue,group']
    .concat(Array.from({ length: 200 }, (_, i) => `u_${name}_${i},${100 + (i % 10)},${i % 2 === 0 ? 'A' : 'B'}`))
    .join('\n')
  const badFile = `recover_${Date.now()}.csv`
  await uploadDataset(request, badCsv, badFile)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  // Select it -> reserved-column error, Run disabled (Part 1 proactive block).
  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(badFile)
  await page.getByTitle(badFile).click()
  await expect(page.getByText(/reserved column "group"/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run analysis' })).toBeDisabled()

  // Fix the dataset: Edit -> expand the Columns section -> remove 'group' -> Save.
  await page.goto('/datasets')
  const row = page.getByRole('row', { name: new RegExp(badFile) })
  await row.hover()
  await row.getByRole('button', { name: 'Edit' }).click()
  const dialog = page.getByRole('dialog').filter({ hasText: 'Edit dataset' })
  await expect(dialog).toBeVisible()
  await dialog.getByText('Columns (types & removal)').click()
  await dialog.getByLabel('remove-column-group').click()
  await expect(dialog.getByLabel('restore-column-group')).toBeVisible()
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).not.toBeVisible({ timeout: 10_000 })

  // Back to Analysis: re-select the (now clean) dataset and run successfully.
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()
  const datasetSelect2 = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect2.click()
  await datasetSelect2.fill(badFile)
  await page.getByTitle(badFile).click()
  await expect(page.getByText(/Data ready:/)).toBeVisible()
  await expect(page.getByText(/reserved column/)).toHaveCount(0)

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 30_000 })
})
