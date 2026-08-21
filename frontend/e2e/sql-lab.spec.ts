import { test, expect } from '@playwright/test'
import type { APIRequestContext, Page } from '@playwright/test'
import { loginViaUi } from './helpers'

/**
 * SQL Lab package: интерактивный редактор, плейсхолдеры дат и замыкание цикла
 * («дизайн на базовом окне → тест → одна кнопка собирает результаты за период
 * теста»).
 *
 * Как и database-connections.spec.ts, ссылается на ТУ ЖЕ postgres, что поднят
 * в стеке: из контейнера бэкенда (который и выполняет SQL) она доступна как
 * "postgres" — не с этой машины. Без E2E_POSTGRES_* тест пропускается.
 */
const PG = {
  host: process.env.E2E_POSTGRES_HOST,
  port: process.env.E2E_POSTGRES_PORT,
  user: process.env.E2E_POSTGRES_USER,
  password: process.env.E2E_POSTGRES_PASSWORD,
  db: process.env.E2E_POSTGRES_DB,
}
const API = '/api/v1'

/** Подключение к собственному postgres стека — через API, а не через форму:
 * саму форму подключения покрывает database-connections.spec.ts, здесь она
 * была бы длинной прелюдией не к тому, что проверяется. */
async function createConnection(request: APIRequestContext, name: string): Promise<string> {
  const resp = await request.post(`${API}/admin/db-connections`, {
    data: {
      display_name: name,
      engine: 'postgresql',
      host: PG.host,
      port: Number(PG.port),
      database: PG.db,
      username: PG.user,
      password: PG.password,
    },
  })
  if (!resp.ok()) throw new Error(`connection create failed: ${resp.status()} ${await resp.text()}`)
  return (await resp.json()).id
}

/** Второй шаг создания датасета — подтверждение колонок (см.
 * database-connections.spec.ts, тот же контракт). */
async function finishDatasetCreation(page: Page) {
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText(/Dataset created\. Confirm the columns/)).toBeVisible({
    timeout: 30_000,
  })
  await dialog.getByRole('button', { name: 'Finish' }).click()
  await expect(dialog).not.toBeVisible({ timeout: 20_000 })
}

test('SQL Lab runs a query, shows the grid and history, and hands the query off to dataset creation', async ({
  page,
}) => {
  test.skip(!PG.host || !PG.password, 'E2E_POSTGRES_* not set — see .github/workflows/ci.yml')
  test.setTimeout(90_000)
  const pageErrors: string[] = []
  page.on('pageerror', (err) => pageErrors.push(err.message))

  await loginViaUi(page)
  const connectionName = `e2e_lab_${Date.now()}`
  await createConnection(page.request, connectionName)

  await page.goto('/sql-lab')
  await page.getByRole('combobox', { name: 'sql-lab-connection' }).click()
  await page.getByTitle(new RegExp(connectionName)).click()

  const editor = page.getByRole('textbox', { name: 'sql-lab-editor' })
  await editor.fill('SELECT id, email, role FROM users')
  await page.getByRole('button', { name: 'Run' }).click()

  await expect(page.getByRole('columnheader', { name: 'email' })).toBeVisible({ timeout: 20_000 })
  // Счетчик строк и время — то, ради чего сюда и заходят вместо превью в
  // модалке создания датасета.
  await expect(page.getByText(/\d+ rows/).first()).toBeVisible()

  // История пополняется КАЖДЫМ прогоном, и клик по строке возвращает запрос
  // в редактор — иначе «а что я запускал 10 минут назад» невосстановимо.
  await page.getByText(/Query history \(/).click()
  const historyRow = page.getByRole('row').filter({ hasText: 'SELECT id, email, role FROM users' })
  await expect(historyRow.first()).toBeVisible({ timeout: 10_000 })
  await editor.fill('SELECT 1')
  await historyRow.first().click()
  await expect(editor).toHaveValue('SELECT id, email, role FROM users')

  // Передача в создание датасета: модалка открывается на вкладке From SQL с
  // уже подставленными подключением и запросом — переписывать их руками и
  // означало бы, что SQL Lab ничего не сэкономил.
  await page.getByRole('button', { name: 'Create dataset from query' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByPlaceholder('SELECT user_id, revenue FROM events WHERE ...')).toHaveValue(
    'SELECT id, email, role FROM users',
  )

  const datasetName = `e2e_lab_ds_${Date.now()}`
  await dialog.getByPlaceholder('e.g. active_users_30d').fill(datasetName)
  await dialog.getByRole('button', { name: 'Create dataset' }).click()
  await finishDatasetCreation(page)

  await page.goto('/datasets')
  await expect(page.getByRole('row', { name: new RegExp(datasetName) })).toBeVisible({
    timeout: 15_000,
  })

  expect(pageErrors).toEqual([])
})

test('a viewer sees no SQL Lab entry, and the page itself refuses a direct link', async ({ page }) => {
  // SQL Lab — Editor+. Показать пункт меню и встретить viewer'а страницей, где
  // любое действие возвращает 403, — та же загадка, что задизейбленная кнопка
  // без объяснения; поэтому гейт в двух местах, и оба проверяются.
  await loginViaUi(page, 'viewer@e2e.test', 'e2epass123')
  await expect(page.getByRole('menuitem', { name: 'Datasets' })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'SQL Lab' })).toHaveCount(0)

  await page.goto('/sql-lab')
  // RequireAuth уводит на главную — редактор не должен ни появиться, ни
  // мигнуть на экране до первого 403 от сервера.
  await expect(page).not.toHaveURL(/\/sql-lab/)
  await expect(page.getByRole('textbox', { name: 'sql-lab-editor' })).toHaveCount(0)
})
