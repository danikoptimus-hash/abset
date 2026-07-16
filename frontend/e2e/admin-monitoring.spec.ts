import { test, expect } from '@playwright/test'
import { loginViaUi } from './helpers'

// Admin monitoring panel: resource usage + persistent history. The
// collector's own 60s timer is too slow for a deterministic e2e test, so
// this forces a snapshot via the admin-only POST endpoint first (the same
// mechanism a real admin would use for "refresh now"), then checks the tab
// actually renders it.
test('admin sees the Monitoring tab with live data after a forced snapshot', async ({ page }) => {
  await loginViaUi(page, 'admin@e2e.test', 'e2epass123')

  const snapshotResp = await page.request.post('/api/v1/admin/monitoring/snapshot-now')
  expect(snapshotResp.ok()).toBeTruthy()
  const snapshot = await snapshotResp.json()
  expect(snapshot.backend_rss_mb).toBeGreaterThan(0)

  await page.goto('/admin')
  await page.getByRole('tab', { name: 'Monitoring' }).click()

  // Stat cards — real values, not the loading placeholder ("…") or the
  // empty-state dash ("—"). "Backend memory" appears twice (stat card
  // title + the chart heading below it), so .first() rather than a bare
  // toBeVisible() (which requires exactly one match).
  await expect(page.getByText('Backend memory').first()).toBeVisible()
  await expect(page.getByText('Database size')).toBeVisible()
  await expect(page.getByText(/^\d+(\.\d+)? (MB|GB)$/).first()).toBeVisible({ timeout: 10_000 })

  // Both time-series charts render (echarts-for-react -> canvas, same
  // assertion style as analyze.spec.ts's forest plot).
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 10_000 })
  expect(await page.locator('canvas').count()).toBeGreaterThanOrEqual(2)

  // Top 10 tables by size — a real Postgres table name should show up.
  await expect(page.getByText('Top 10 tables by size')).toBeVisible()
  await expect(page.getByRole('cell', { name: /\.users$/ })).toBeVisible()

  // Range picker switches to a longer (hourly-resolution) window without
  // erroring — an empty history at that resolution is fine (fresh e2e
  // stack has no hour-old data yet), just checking the control works and
  // the panel doesn't fall over.
  await page.getByText('7d', { exact: true }).click()
  await expect(page.getByText('Backend memory').first()).toBeVisible()
})

test('editor cannot reach the Monitoring tab (redirected away from /admin)', async ({ page }) => {
  await loginViaUi(page, 'admin@e2e.test', 'e2epass123')
  const email = `editor_monitor_${Date.now()}@e2e.test`
  const createResp = await page.request.post('/api/v1/admin/users', {
    data: { email, first_name: 'Editor', last_name: 'Monitor', role: 'editor', password: 'e2epass123' },
  })
  expect(createResp.ok()).toBeTruthy()

  await page.getByTestId('user-menu-trigger').click()
  await page.getByText('Logout').click()
  await expect(page).toHaveURL(/\/login$/)

  await loginViaUi(page, email, 'e2epass123')
  await page.goto('/admin')
  await expect(page).toHaveURL(/\/experiments$/)
  await expect(page.getByText('Monitoring')).not.toBeVisible()
})
