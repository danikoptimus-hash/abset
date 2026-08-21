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

async function pollJob(request: APIRequestContext, jobId: string): Promise<Record<string, unknown>> {
  for (let i = 0; i < 300; i++) {
    const job = await (await request.get(`${API}/jobs/${jobId}`)).json()
    if (job.status === 'completed') return job
    if (job.status === 'failed') throw new Error(`job failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error('job did not finish in time')
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

test('a dataset query with {{date_from}}/{{date_to}} materializes for the chosen period and re-runs for a new one', async ({
  page,
}) => {
  test.skip(!PG.host || !PG.password, 'E2E_POSTGRES_* not set — see .github/workflows/ci.yml')
  test.setTimeout(90_000)

  await loginViaUi(page)
  const connectionName = `e2e_params_${Date.now()}`
  await createConnection(page.request, connectionName)

  await page.goto('/datasets')
  await page.getByRole('button', { name: 'Dataset' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('tab', { name: 'From SQL' }).click()
  await dialog.getByRole('combobox', { name: 'from-sql-connection-select' }).click()
  await page.getByTitle(new RegExp(connectionName)).click()

  // Поля дат не существуют, пока в запросе нет плейсхолдеров — показывать их
  // всегда значило бы просить заполнить то, что ни на что не влияет.
  await expect(dialog.getByLabel('param-date-from')).toHaveCount(0)

  const sqlBox = dialog.getByPlaceholder('SELECT user_id, revenue FROM events WHERE ...')
  await sqlBox.fill(
    'SELECT id, email FROM users WHERE created_at >= {{date_from}}::timestamptz ' +
      'AND created_at < {{date_to}}::timestamptz',
  )
  const dateFrom = dialog.getByLabel('param-date-from')
  await expect(dateFrom).toBeVisible({ timeout: 15_000 })

  const datasetName = `e2e_params_ds_${Date.now()}`
  await dialog.getByPlaceholder('e.g. active_users_30d').fill(datasetName)
  // Пока даты не заданы, создавать нечего: незаполненный плейсхолдер — это
  // незаконченная форма, а не ошибка сервера после долгой выгрузки.
  await expect(dialog.getByRole('button', { name: 'Create dataset' })).toBeDisabled()

  await dateFrom.fill('2000-01-01')
  await page.keyboard.press('Enter')
  await dialog.getByLabel('param-date-to').fill('2100-01-01')
  await page.keyboard.press('Enter')
  await dialog.getByRole('button', { name: 'Create dataset' }).click()
  await finishDatasetCreation(page)

  const listResp = await page.request.get(`${API}/datasets?page_size=200`)
  const created = (await listResp.json()).items.find((d: { filename: string }) =>
    d.filename.startsWith(datasetName),
  )
  expect(created).toBeTruthy()
  expect(created.param_date_from).toBe('2000-01-01')
  // Хранится ШАБЛОН, а не подставленный текст — иначе повторный сбор за другой
  // период стал бы невозможен после первой же материализации.
  expect(created.sql_text).toContain('{{date_from}}')
  expect(created.n_rows).toBeGreaterThan(0)

  // Правка ТОЛЬКО периода (запрос не тронут) — самая частая правка такого
  // датасета, и она обязана перевыполнить запрос, а не тихо ничего не сделать.
  const row = page.getByRole('row', { name: new RegExp(datasetName) })
  await row.hover()
  await row.getByRole('button', { name: 'Edit' }).click()
  const editDialog = page.getByRole('dialog').filter({ hasText: 'Edit dataset' })
  await expect(editDialog.getByLabel('param-date-from')).toHaveValue('2000-01-01', {
    timeout: 15_000,
  })
  await editDialog.getByLabel('param-date-from').fill('2001-02-03')
  await page.keyboard.press('Enter')
  await editDialog.getByRole('button', { name: 'Save' }).click()
  await page
    .getByRole('dialog')
    .filter({ hasText: 'Save changes?' })
    .getByRole('button', { name: 'Save & refresh' })
    .click()
  await expect(editDialog).not.toBeVisible({ timeout: 30_000 })

  const afterResp = await page.request.get(`${API}/datasets?page_size=200`)
  const after = (await afterResp.json()).items.find((d: { filename: string }) =>
    d.filename.startsWith(datasetName),
  )
  expect(after.param_date_from).toBe('2001-02-03')
})
