import { test, expect } from '@playwright/test'
import { clickSelectOption, loginViaUi, seedExperiment, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

// Reserved-column collision is caught BEFORE a job is queued.
//
// История этого теста стоит того, чтобы ее знать, иначе он сломается в
// третий раз. Изначально он ловил регрессию compare-methods: упавшая
// analyze-джоба обязана показывать СВОЮ ошибку (job.error), а не общее
// "Failed to get job status". Настоящий OOM в e2e не воспроизвести, поэтому
// брали детерминированный триггер — датасет, на котором анализ падает
// по-настоящему. Триггер меняли уже дважды, и оба раза по одной причине:
// приложение училось ловить эту ошибку РАНЬШЕ, на клиенте.
//   1. дубликаты unit id -> перекрыты гардом "выбери колонку даты";
//   2. своя колонка "group" -> перекрыта гардом зарезервированных колонок
//      (0df8e26, "fix(analyze): validation errors must not replace the
//      analysis form").
//
// Гоняться за третьим триггером незачем: исходная цель — «упавшая джоба
// показывает свою настоящую ошибку» — полностью покрыта соседним тестом
// (post-data без unit-колонки: он проходит клиентские проверки, реально
// запускает джобу и ждет ее собственный текст ошибки). А вот сам гард
// 0df8e26 не покрыт ничем, хотя это и есть нынешнее ЗАДУМАННОЕ поведение:
// не дать поставить заведомо провальную джобу в очередь.
test('a dataset with reserved columns is refused up front, before any job is queued', async ({
  page,
  request,
}) => {
  const name = `analyze_reserved_col_e2e_${Date.now()}`
  await seedExperiment(request, name)

  // Своя колонка "group" сталкивается с колонкой назначений (ref edb716f1).
  const csv =
    'user_id,revenue,group\n' +
    Array.from({ length: 50 }, (_, i) => `u${i},${100},control`).join('\n')
  const collisionFilename = `group_collision_${Date.now()}.csv`
  await uploadDataset(request, csv, collisionFilename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(collisionFilename)
  await page.getByTitle(collisionFilename).click()

  // Вместо "Data ready" — объяснение, ЧТО именно не так и что делать.
  await expect(page.getByText(/collide with ABSet's own/)).toBeVisible()
  await expect(page.getByText(new RegExp(`Data ready: ${collisionFilename.replace('.', '\\.')}`))).toBeHidden()

  // Кнопка заблокирована — джоба не ставится в очередь вовсе.
  const runButton = page.getByRole('button', { name: 'Run analysis' })
  await expect(runButton).toBeDisabled()

  // 0df8e26: ошибка валидации показывается ВМЕСТО результата, но НЕ заменяет
  // собой форму — датасет можно перевыбрать, не перезагружая страницу.
  await expect(datasetSelect).toBeVisible()

  // И ничего похожего на общую ошибку поллинга (исходный повод для теста).
  await expect(page.getByText('Failed to get job status')).not.toBeVisible()

  // Джоба действительно не создавалась: у эксперимента нет результатов.
  const results = await request.get(`${API_BASE}/experiments/${name}/results`)
  expect([404, 200]).toContain(results.status())
  if (results.status() === 200) {
    expect((await results.json()).results ?? []).toHaveLength(0)
  }
})

// Regression for a real production internal_error report: post-period data
// uploaded without the design's unit-id column crashed with a raw pandas
// KeyError (data[self.config.unit_col] was never guarded) — surfaced as an
// opaque "Internal processing error" instead of a clear, actionable message.
// None of the existing analyze e2e coverage used a post-dataset missing the
// unit_col column, which is exactly why this slipped through untested.
test('analyzing with post-data missing the unit column shows a clear error, not Internal processing error', async ({
  page,
  request,
}) => {
  const name = `analyze_missing_unit_col_e2e_${Date.now()}`
  await seedExperiment(request, name)

  const csv = 'not_user_id,revenue\n' + Array.from({ length: 50 }, (_, i) => `u${i},${100 + (i % 10)}`).join('\n')
  const filename = `missing_unit_col_${Date.now()}.csv`
  await uploadDataset(request, csv, filename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible()

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(page.getByText(/Unit column 'user_id' is not in the uploaded data/)).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByText('Internal processing error')).not.toBeVisible()
})

// Regression for the compare-methods OOM bug itself: with Bootstrap
// explicitly added to the metric's method selection (item 3, consolidated
// package — replaces the old "Compare alternative methods" checkbox),
// Bootstrap (the method that used to crash the process at scale) runs as
// one of the comparison methods and must complete and render normally at
// ordinary data sizes.
test('Selecting Bootstrap as an extra method completes and shows it in the detailed results', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `analyze_compare_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const methodSelect = page.getByRole('combobox', { name: 'method-select-revenue' })
  await methodSelect.click()
  await expect(page.locator('.ant-select-item-option-content').first()).toBeVisible()
  await clickSelectOption(page, 'Bootstrap (bca)')
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByText(/Bootstrap/).first()).toBeVisible()
  // No row failed silently-crashed the whole table render.
  await expect(page.getByText('Failed to get job status')).not.toBeVisible()
})
