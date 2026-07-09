import { test, expect } from '@playwright/test'
import { loginViaUi } from './helpers'

test('viewer does not see "Create A/B Test" button', async ({ page }) => {
  await loginViaUi(page, 'viewer@e2e.test', 'e2epass123')
  await expect(page.getByRole('button', { name: 'Create A/B Test' })).not.toBeVisible()
})

test('viewer cannot open /admin (redirected away)', async ({ page }) => {
  await loginViaUi(page, 'viewer@e2e.test', 'e2epass123')
  await page.goto('/admin')
  await expect(page).toHaveURL(/\/experiments$/)
})

test('admin sees "Create A/B Test" and can open /admin', async ({ page }) => {
  await loginViaUi(page, 'admin@e2e.test', 'e2epass123')
  await expect(page.getByRole('button', { name: 'Create A/B Test' })).toBeVisible()

  await page.goto('/admin')
  await expect(page).toHaveURL(/\/admin$/)
  await expect(page.getByText('Users')).toBeVisible()
})
