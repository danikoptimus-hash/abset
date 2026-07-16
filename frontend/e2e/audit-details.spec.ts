import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

// Item 4 (audit-details package): status_change already carried {from,to}
// in details, but neither the History tab nor the Audit page ever rendered
// it — this is the regression test for the new Details column showing the
// human-readable "status: designed → running" line, not just the bare
// action string.
test('Changing experiment status shows a human-readable summary in the History tab', async ({ page, request }) => {
  const name = `audit_details_status_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  await page.getByText('designed', { exact: true }).click()
  await page.getByRole('menuitem', { name: 'Move to running' }).click()
  await expect(page.getByText('running', { exact: true })).toBeVisible()

  await page.getByRole('tab', { name: 'History' }).click()
  const row = page.getByRole('row').filter({ hasText: 'experiment.status_change' })
  await expect(row).toBeVisible()
  await expect(row.getByText('status: designed → running')).toBeVisible()
})
