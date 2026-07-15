import { test, expect } from '@playwright/test'
import { loginViaUi } from './helpers'

// UX contract, part B (CLAUDE.md query-key registry): a mutation on one page
// must be visible on another page reached via SPA navigation, without a full
// reload — the datasets list and the design wizard's dataset picker read the
// same data through two different cache keys (`datasets` vs
// `datasets-for-select`), so creating a dataset must invalidate both.
//
// The other two B.4 checks (tag created in Properties -> visible in the
// list's tag filter without reload; analysis completing -> Results tab
// updates without reload) are already exercised, incidentally but really, by
// tags.spec.ts and analyze.spec.ts — both stay on one page load throughout
// the create-then-observe sequence, so they'd have failed before this
// package's invalidation fixes and continue to pass after them.
test('a dataset created via the Datasets page is immediately selectable in the design wizard, no reload', async ({
  page,
}) => {
  test.setTimeout(30_000)
  await loginViaUi(page)
  await page.goto('/datasets')

  const filename = `cache_freshness_e2e_${Date.now()}.csv`
  await page.getByRole('button', { name: 'Dataset' }).click()
  const dialog = page.getByRole('dialog').filter({ hasText: 'New dataset' })
  await expect(dialog).toBeVisible()

  await dialog.locator('input[type="file"]').setInputFiles({
    name: filename,
    mimeType: 'text/csv',
    buffer: Buffer.from('user_id,revenue\nu1,10\nu2,20\n'),
  })
  // Item 1.1: upload now lands on a rename-confirm step instead of closing
  // immediately — Finish (keeping the prefilled defaults) is what actually
  // creates the dataset and closes the modal.
  await expect(page.getByLabel('rename-dataset-name')).toHaveValue(filename, { timeout: 10_000 })
  await page.getByRole('button', { name: 'Finish' }).click()
  await expect(dialog).not.toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('row', { name: new RegExp(filename) })).toBeVisible()

  // SPA navigation only (nav link + button clicks) — no page.reload/goto in
  // between, so a stale `datasets-for-select` cache would still show up here.
  await page.getByRole('link', { name: 'A/B Tests' }).click()
  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  const datasetSelect = page.getByRole('combobox', { name: 'design-dataset-select' })
  await datasetSelect.click()
  await expect(page.getByTitle(new RegExp(filename))).toBeVisible()
})
