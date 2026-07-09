import { test, expect } from '@playwright/test'

test('login with valid credentials redirects to experiments list', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('Email').fill('admin@e2e.test')
  await page.getByLabel('Password').fill('e2epass123')
  await page.getByRole('button', { name: 'Sign In' }).click()

  await expect(page).toHaveURL(/\/experiments$/)
  await expect(page.getByTestId('user-menu-trigger')).toBeVisible()
})

test('login with wrong password shows an error and stays on the login page', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('Email').fill('admin@e2e.test')
  await page.getByLabel('Password').fill('wrong-password')
  await page.getByRole('button', { name: 'Sign In' }).click()

  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page).toHaveURL(/\/login$/)
})

test('logout redirects back to login', async ({ page }) => {
  await page.goto('/login')
  await page.getByLabel('Email').fill('admin@e2e.test')
  await page.getByLabel('Password').fill('e2epass123')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/experiments$/)

  await page.getByTestId('user-menu-trigger').click()
  await page.getByText('Logout').click()

  await expect(page).toHaveURL(/\/login$/)
})

test('unauthenticated visit to /experiments redirects to /login', async ({ page }) => {
  await page.goto('/experiments')
  await expect(page).toHaveURL(/\/login$/)
})
