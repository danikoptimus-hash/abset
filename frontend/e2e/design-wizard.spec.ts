import { test, expect } from '@playwright/test'
import { loginViaUi } from './helpers'

// FRONTEND.md §7 R5: "Playwright: e2e создание теста на демо-данных ->
// страница теста -> publish -> edit блока «Гипотеза»."
test('create experiment via wizard on demo data, then publish and edit hypothesis', async ({ page }) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Создать A/B тест' }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  // Шаг 1: демо-данные
  await page.getByRole('button', { name: 'Демо-данные' }).click()
  await expect(page.getByText(/Данные загружены: 5000 строк/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Далее' }).click()

  // Шаг 2: имя эксперимента (демо уже предзаполнило группы/метрики)
  const expName = `wizard_e2e_${Date.now()}`
  await page.getByPlaceholder('Имя эксперимента').fill(expName)
  await page.getByRole('button', { name: 'Далее' }).click()

  // Шаг 3: параметры — isolation=off (устойчиво к повторным e2e-прогонам на
  // одной БД: демо-данные детерминированы, seed=0 -> одни и те же 5000
  // юзеров каждый раз; "exclude" по умолчанию исключил бы юзеров, занятых
  // экспериментами из предыдущих прогонов, оставляя 0 кандидатов).
  await page.getByText(/exclude — исключить участников/).click()
  await page.getByText(/off — не исключать никого/).click()
  await page.getByRole('button', { name: 'Далее' }).click()

  // Шаг 4: запуск
  await page.getByRole('button', { name: 'Спроектировать' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  // Страница теста: видна конфигурация и MDE-таблица
  await expect(page.getByText('Дизайн')).toBeVisible()
  await expect(page.getByText('MDE-таблица')).toBeVisible()

  // Publish
  await expect(page.getByText('draft', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Publish' }).click()
  await expect(page.getByText('published', { exact: true })).toBeVisible()

  // Edit -> изменить блок "Гипотеза" -> Save
  await page.getByRole('button', { name: 'Edit' }).click()
  const hypothesisTextarea = page.locator('textarea').first()
  await hypothesisTextarea.fill('Новая гипотеза из e2e-теста')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Сохранено')).toBeVisible()
  await expect(page.getByText('Новая гипотеза из e2e-теста')).toBeVisible()
})
