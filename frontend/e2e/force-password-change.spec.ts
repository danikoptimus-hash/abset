import { test, expect } from '@playwright/test'
import { createUserWithTempPassword } from './helpers'

test('user with temp password is forced to change it before accessing the app', async ({
  page,
  request,
}) => {
  const email = `forcepw_${Date.now()}@e2e.test`
  const tempPassword = await createUserWithTempPassword(request, email)

  await page.goto('/login')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(tempPassword)
  await page.getByRole('button', { name: 'Sign In' }).click()

  // Any navigation (not just a direct visit) redirects to /profile.
  await expect(page).toHaveURL(/\/profile$/)
  await expect(page.getByText('Change Password')).toBeVisible()

  await page.goto('/experiments')
  await expect(page).toHaveURL(/\/profile$/)

  await page.getByLabel('Current Password').fill(tempPassword)
  await page.getByLabel('New Password').fill('newpassword456')
  await page.getByRole('button', { name: 'Change Password' }).click()

  await expect(page).toHaveURL(/\/experiments$/)

  // After the change, access is open; a repeat login with the old password fails.
  await page.getByTestId('user-menu-trigger').click()
  await page.getByText('Logout').click()
  await expect(page).toHaveURL(/\/login$/)

  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill('newpassword456')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/experiments$/)
})
