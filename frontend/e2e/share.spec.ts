import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

// Кнопка Share на странице теста (пакет share+folders).
//
// Тост про draft проверяется здесь, а не юнитом: сам ТЕКСТ покрыт
// src/lib/share.test.ts, а тут — что он реально доезжает до пользователя по
// клику именно на черновике.

test('Share on a draft copies the link and warns that recipients cannot open it', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `_dev_share_draft_${Date.now()}`
  await seedExperiment(request, name)

  // Playwright's chromium: буфер обмена под https/localhost доступен, но
  // разрешение нужно выдать явно — иначе clipboard API отклонит и мы
  // проверили бы fallback вместо основного пути.
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await loginViaUi(page)
  await page.goto(`/experiments/${encodeURIComponent(name)}`)

  // Seed создает тест черновиком — убеждаемся, что это так, иначе тест
  // проверял бы не ту ветку.
  await expect(page.getByText('draft', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Share' }).click()

  await expect(page.getByText(/Link copied\. Note: this experiment is a draft/)).toBeVisible()
  await expect(
    page.getByText(/only you, explicitly granted users, and Admins can open it/),
  ).toBeVisible()

  // Скопировано именно id-шное (переживающее ренейм), а не именное.
  const copied = await page.evaluate(() => navigator.clipboard.readText())
  expect(copied).toContain('/experiments/by-id/')
  expect(copied).not.toContain(encodeURIComponent(name))

  // И эта ссылка действительно открывает тест (redirect на именной URL).
  await page.goto(copied)
  await expect.poll(() => page.url()).toContain(encodeURIComponent(name))
  await expect(page.getByRole('heading', { name })).toBeVisible()
})

test('Share on a published experiment copies without the draft warning', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `_dev_share_pub_${Date.now()}`
  await seedExperiment(request, name)
  await request.patch(`${API_BASE}/experiments/${encodeURIComponent(name)}`, {
    data: { publication_status: 'published' },
  })

  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await loginViaUi(page)
  await page.goto(`/experiments/${encodeURIComponent(name)}`)

  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Share' }).click()

  await expect(page.getByText('Link copied', { exact: true })).toBeVisible()
  await expect(page.getByText(/draft/)).toHaveCount(0)
})

test('A shared draft link shows a proper not-found page to someone without access', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `_dev_share_denied_${Date.now()}`
  // Черновик, созданный админом: viewer его не видит (CLAUDE.md, видимость).
  await seedExperiment(request, name)

  const resolved = await request.get(
    `${API_BASE}/experiments/${encodeURIComponent(name)}`,
  )
  const experimentId = (await resolved.json()).id as string
  expect(experimentId).toBeTruthy()

  await loginViaUi(page, 'viewer@e2e.test', 'e2epass123')
  await page.goto(`/experiments/by-id/${experimentId}`)

  // Внятный экран, а не пустая страница и не бесконечный спиннер.
  await expect(page.getByText('Experiment not found')).toBeVisible()
})

test('Share is available to a viewer on an experiment they can see', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `_dev_share_viewer_${Date.now()}`
  await seedExperiment(request, name)
  await request.patch(`${API_BASE}/experiments/${encodeURIComponent(name)}`, {
    data: { publication_status: 'published' },
  })

  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
  await loginViaUi(page, 'viewer@e2e.test', 'e2epass123')
  await page.goto(`/experiments/${encodeURIComponent(name)}`)

  // Viewer не может ни править, ни экспортировать — но "⋯" ему теперь виден
  // ради Share (поделиться = прочитать).
  await page.getByRole('button', { name: 'More actions' }).click()
  await expect(page.getByRole('menuitem', { name: 'Share' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'Export' })).toHaveCount(0)
  await expect(page.getByRole('menuitem', { name: 'Delete' })).toHaveCount(0)

  await page.getByRole('menuitem', { name: 'Share' }).click()
  await expect(page.getByText('Link copied', { exact: true })).toBeVisible()
})
