import { test, expect, type APIRequestContext } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

async function designAbsplit(request: APIRequestContext, name: string, datasetId: string) {
  const resp = await request.post(`${API_BASE}/design`, {
    data: {
      config: {
        name, unit_col: 'user_id',
        groups: { control: 0.5, treatment: 0.5 },
        metrics: [{ name: 'revenue', type: 'continuous', role: 'primary' }],
        sample_size: 100, split_method: 'simple', isolation: 'off',
      },
      dataset_id: datasetId,
    },
  })
  if (!resp.ok()) throw new Error(`design failed: ${resp.status()}`)
  const { job_id } = await resp.json()
  for (let i = 0; i < 80; i++) {
    const job = await (await request.get(`${API_BASE}/jobs/${job_id}`)).json()
    if (job.status === 'completed') return
    if (job.status === 'failed') throw new Error(`design job failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error('design job did not finish')
}

// Feature (dataset name in downloads): the design report download filename
// carries the design dataset's name — <experiment>_<dataset>_design_report.html.
test('design report download filename contains the design dataset name', async ({ page, request }) => {
  test.setTimeout(60_000)

  const rows = ['user_id,revenue']
  for (let i = 0; i < 120; i++) rows.push(`u${i},${100 + (i % 9)}`)
  const filename = `quarterly sales ${Date.now()}.csv` // spaces → sanitized to "quarterly_sales_..."
  const { id: datasetId } = await uploadDataset(request, rows.join('\n'), filename)

  const name = `dl_name_e2e_${Date.now()}`
  await designAbsplit(request, name, datasetId)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  // Target the report-download anchor by its exact href — several
  // "Download …" links sit together on the Design tab.
  const reportLink = page.locator(
    `a[href="/api/v1/experiments/${name}/reports/design_report.html?download=1"]`,
  )
  await expect(reportLink).toBeVisible()
  // Дождаться и СОСЕДЕЙ по строке: кнопки отдельных sample-файлов приезжают
  // ОТДЕЛЬНЫМ запросом и рендерятся ВЫШЕ отчёта в том же `Space wrap`. Если
  // они долетают после проверки видимости (а под нагрузкой это случается),
  // строка переносится ровно в момент клика — и клик уезжает на соседнюю
  // кнопку. Ровно так этот спек и падал в полном прогоне: скачивался
  // samples.zip вместо design_report.html.
  await expect(page.locator(`a[href="/api/v1/experiments/${name}/samples.zip"]`)).toBeVisible()
  await expect(page.locator('a[href*="/samples/"]').first()).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await reportLink.click()
  const download = await downloadPromise

  expect(download.url()).toContain('design_report.html')
  const suggested = download.suggestedFilename()
  expect(suggested).toContain('quarterly_sales')
  expect(suggested).toContain('design_report.html')
})
