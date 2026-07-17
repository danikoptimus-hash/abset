import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

// Пакет UI-фиксов: icon-only кнопки (item 1), "⋯" крайним справа (item 2),
// markdown в подсказках визарда (item 3).
//
// Существующие спеки уже дергают Bulk select / Import / Edit через
// getByRole('button', {name}) — оно матчит accessible name, т.е. наш новый
// aria-label, поэтому они продолжают работать без правок. Здесь — то, чего в
// них нет: tooltip, порядок в шапке, отсутствие сырого markdown.

test('Import button is icon-only but still shows its label as a tooltip and opens the modal', async ({
  page,
}) => {
  await loginViaUi(page)
  await page.goto('/experiments')

  const importButton = page.getByRole('button', { name: 'Import' })
  await expect(importButton).toBeVisible()
  // Подпись убрана — на самой кнопке текста "Import" быть не должно.
  await expect(importButton).toHaveText('')

  // ...но по наведению всплывает tooltip с прежней подписью (тот же паттерн,
  // что list-ux.spec.ts на аватарке владельца — AntD ставит role="tooltip").
  await importButton.hover()
  await expect(page.getByRole('tooltip')).toContainText('Import')

  // И кнопка по-прежнему рабочая.
  await importButton.click()
  await expect(page.getByRole('dialog').filter({ hasText: 'Import A/B test' })).toBeVisible()
})

test('Bulk select is icon-only and still toggles bulk mode', async ({ page, request }) => {
  const name = `_dev_uifix_bulk_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto('/experiments')

  const bulk = page.getByRole('button', { name: 'Bulk select' })
  await expect(bulk).toHaveText('')
  await bulk.click()
  // В bulk-режиме та же кнопка становится Cancel (иконка+aria-label меняются).
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible()
})

test('On the experiment page the "⋯" menu is the rightmost header action and still opens', async ({
  page,
  request,
}) => {
  const name = `_dev_uifix_menu_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${encodeURIComponent(name)}`)

  const edit = page.getByRole('button', { name: 'Edit', exact: true })
  const more = page.getByRole('button', { name: 'More actions' })
  await expect(edit).toBeVisible()
  await expect(more).toBeVisible()

  // "⋯" правее Edit (overflow-меню идёт последним).
  const editBox = await edit.boundingBox()
  const moreBox = await more.boundingBox()
  expect(editBox && moreBox && moreBox.x > editBox.x).toBeTruthy()

  // Меню открывается и выровнено по правому краю (bottomRight): его правая
  // граница не уезжает за кнопку/вьюпорт.
  await more.click()
  await expect(page.getByRole('menuitem', { name: 'Share' })).toBeVisible()
  const menu = page.locator('.ant-dropdown-menu').last()
  const menuBox = await menu.boundingBox()
  expect(menuBox && menuBox.x + menuBox.width <= page.viewportSize()!.width + 1).toBeTruthy()
})

test('Wizard "what is this data" help renders markdown, not literal ** or dashes', async ({
  page,
}) => {
  await loginViaUi(page)
  await page.goto('/experiments/new')

  // Раскрываем панель через её header-кнопку (AntD Collapse header —
  // role="button" с aria-expanded), это надёжнее клика по текстовому узлу.
  await page.getByRole('button', { name: /What is this data and what should it contain/ }).click()

  // Смысловой текст на месте...
  await expect(page.getByText('one row = one user')).toBeVisible()
  // ...а "Format:" реально отрисован как <strong> (markdown сработал).
  await expect(page.locator('strong', { hasText: 'Format:' })).toBeVisible()

  // ...и нигде в раскрытой панели нет сырого markdown (литеральных **).
  const item = page.locator('.ant-collapse-item', { hasText: 'What is this data' })
  expect(await item.innerText()).not.toContain('**')
})
