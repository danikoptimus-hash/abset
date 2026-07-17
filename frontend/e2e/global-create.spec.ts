import { test, expect } from '@playwright/test'
import { loginViaUi } from './helpers'

// Глобальная "+" в шапке (Superset-style, Editor+): дополнительный вход в
// создание из любого места приложения. Два пункта открываются по-разному —
// "A/B test" роутом, "Dataset" модалкой (роута /datasets/new нет), — поэтому
// проверяются оба, а не только наличие меню.

test('Global "+" is visible for an editor and both entries navigate', async ({ page }) => {
  await loginViaUi(page)

  // Пункты ищем ВНУТРИ выпадающего меню: верхняя навигация — тоже menuitem'ы
  // ("A/B Tests"/"Datasets"), и без этого скоупа "A/B test"/"Dataset"
  // разрешаются в два элемента каждый (strict mode violation).
  const dropdown = page.locator('.ant-dropdown-menu')

  // A/B test -> визард дизайна.
  await page.getByTestId('global-create-trigger').click()
  await dropdown.getByRole('menuitem', { name: 'A/B test', exact: true }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  // Dataset -> модалка создания датасета (роута /datasets/new нет).
  await page.getByTestId('global-create-trigger').click()
  await dropdown.getByRole('menuitem', { name: 'Dataset', exact: true }).click()
  await expect(page.getByRole('dialog').filter({ hasText: 'New dataset' })).toBeVisible()
})

test('Global "+" is absent for a viewer', async ({ page }) => {
  await loginViaUi(page, 'viewer@e2e.test', 'e2epass123')
  await page.goto('/experiments')
  // Ждем, что список реально отрисовался (у страницы нет заголовка, за
  // который можно зацепиться) — иначе "кнопки нет" прошло бы и на пустой,
  // еще не смонтированной странице.
  await expect(page.getByPlaceholder('Search by name or tag...')).toBeVisible()

  await expect(page.getByTestId('global-create-trigger')).toHaveCount(0)
})
