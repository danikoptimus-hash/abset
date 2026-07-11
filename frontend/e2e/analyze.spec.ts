import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment, seedTwoMetricExperiment, uploadDataset } from './helpers'

// FRONTEND.md §7 R6: "Playwright: демо пост-данные -> анализ -> вердикты и
// forest plot видны -> экспорт таблицы."
// UX package (explicit run, item B): preparing demo data no longer runs
// analysis by itself — "Run analysis" is a separate, explicit step.
test('analyze with demo post-data shows verdicts and forest plot, then exports the table', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `analyze_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  // Preparing data must NOT have started analysis on its own.
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).not.toBeVisible()

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await expect(page.getByRole('heading', { name: 'Forest plot' })).toBeVisible()
  // ECharts renders into a canvas — the chart itself can't be checked with a
  // text locator, but the container must exist and be visible.
  await expect(page.locator('canvas').first()).toBeVisible()

  // Detailed table and CSV export live on the Results tab (UX package,
  // section 2: Analysis has verdicts+charts, Results has the table).
  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByText('Detailed Results Table')).toBeVisible()

  // No "Designed" column (UX package, 5.1) — the designed method is bolded
  // instead.
  await expect(page.getByRole('columnheader', { name: 'Designed' })).toHaveCount(0)
  await expect(page.getByRole('columnheader', { name: 'Effect (abs.)' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Lift %' })).toBeVisible()

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export CSV' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toContain('detailed_results.csv')
})

test('Run analysis is disabled with a tooltip until data is prepared', async ({ page, request }) => {
  const name = `analyze_disabled_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const runButton = page.getByRole('button', { name: 'Run analysis' })
  await expect(runButton).toBeDisabled()
  await runButton.hover({ force: true })
  await expect(page.getByText('Select a dataset or generate demo data first')).toBeVisible()

  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await expect(runButton).toBeEnabled()
})

test('Analysis tab layout: options above the dropzone, run button below data', async ({ page, request }) => {
  const name = `analyze_layout_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const optionsBox = await page.getByText('Analysis options', { exact: true }).boundingBox()
  const dataBox = await page.getByText('Data', { exact: true }).boundingBox()
  const dropzoneBox = await page.getByRole('combobox', { name: 'post-period-dataset-select' }).boundingBox()
  const runButtonBox = await page.getByRole('button', { name: 'Run analysis' }).boundingBox()

  expect(optionsBox).not.toBeNull()
  expect(dataBox).not.toBeNull()
  expect(dropzoneBox).not.toBeNull()
  expect(runButtonBox).not.toBeNull()
  expect(optionsBox!.y).toBeLessThan(dataBox!.y)
  expect(dataBox!.y).toBeLessThan(dropzoneBox!.y)
  expect(dropzoneBox!.y).toBeLessThan(runButtonBox!.y)
})

// UX package, п.3: after a first analysis, "Re-run analysis" reopens the
// options+upload panel and a second run (with a different dataset) replaces
// what Results shows — history isn't lost (run_meta.run_number counts up),
// but only the latest run is displayed.
test('re-run analysis with a new dataset updates the results and run count', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `analyze_rerun_e2e_${Date.now()}`
  await seedExperiment(request, name)

  // Uploaded via the API up front, before any page load: DatasetSelect's
  // query is cached per mount and isn't invalidated by an out-of-band API
  // call made later in the browser context's lifetime.
  const csv =
    'user_id,revenue\n' + Array.from({ length: 200 }, (_, i) => `u_${name}_${i},${100 + (i % 10)}.5`).join('\n')
  const rerunFilename = `rerun_${Date.now()}.csv`
  await uploadDataset(request, csv, rerunFilename)

  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()
  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByText(/demo_post_data\.csv \(run #1\)/)).toBeVisible()

  await page.getByRole('tab', { name: 'Analysis' }).click()
  await page.getByRole('button', { name: 'Re-run analysis' }).click()
  await expect(page.getByRole('button', { name: 'Run analysis' })).toBeDisabled()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(rerunFilename)
  await page.getByTitle(rerunFilename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${rerunFilename.replace('.', '\\.')}`))).toBeVisible()

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByText(new RegExp(`${rerunFilename.replace('.', '\\.')} \\(run #2\\)`))).toBeVisible()
})

// UX package, item 1: metric cards on Analysis double as tabs — clicking one
// filters the wall of plots below to that metric only.
test('clicking a metric card on Analysis switches which metric its analytics show', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `analyze_percard_e2e_${Date.now()}`
  await seedTwoMetricExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()
  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // Defaults to the first primary metric (revenue) — its own "primary" card
  // is highlighted as the active tab, and the plot wall below is titled
  // "revenue", not "clicks".
  await expect(page.getByRole('heading', { name: 'revenue', level: 4 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'clicks', level: 4 })).not.toBeVisible()

  const clicksCard = page.getByRole('tab', { name: /clicks/ }).filter({ hasText: 'secondary' })
  await expect(clicksCard).toBeVisible()
  await clicksCard.click()

  await expect(page.getByRole('heading', { name: 'clicks', level: 4 })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'revenue', level: 4 })).not.toBeVisible()
})

// UX package, item 2: compare_methods=True can recompute the exact same
// designed chain as one of its "alternative" chains (revenue here has no
// pre_col, so its designed chain is plain Welch t-test — the same as the
// first alt chain compare_methods_chains() always includes) — Results must
// show that as ONE row, not two differing only by correction/p-adj.
test('Results shows exactly one designed-method row per metric even with compare_methods duplicates, and a CUPED rho column', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `analyze_dedup_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()
  // Compare alternative methods is checked by default (5-part package
  // pt.4) — no need to open Advanced options and check it manually.
  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByRole('columnheader', { name: /CUPED/ })).toBeVisible()

  // exact: compare_methods also runs "RemoveOutliers + Welch t-test" as a
  // genuinely different alternative — its cell text contains "Welch t-test"
  // as a substring too, so a loose match would over-count.
  const welchCells = page.getByRole('cell', { name: 'Welch t-test', exact: true })
  await expect(welchCells).toHaveCount(1)
})

// Item 2: a post-period dataset with duplicate unit ids (day-by-day data)
// makes Date column required — analyze() can't aggregate without knowing
// which column is the date — and Run analysis stays disabled until one is
// picked. Once selected, analysis proceeds normally (data aggregated per
// user under the hood).
test('post-data with duplicate unit ids makes Date column required and blocks Run analysis until selected', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `analyze_dupcheck_e2e_${Date.now()}`
  await seedExperiment(request, name)

  const days = ['2026-01-01', '2026-01-02', '2026-01-03']
  const lines = ['user_id,revenue,event_date']
  for (const day of days) {
    for (let i = 0; i < 100; i++) {
      lines.push(`u_${name}_${i},${100 + (i % 10)}.5,${day}`)
    }
  }
  const filename = `daily_dup_${Date.now()}.csv`
  await uploadDataset(request, lines.join('\n'), filename)

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible()

  await expect(page.getByText(/Dataset contains 100 duplicated unit ids/)).toBeVisible()
  const runButton = page.getByRole('button', { name: 'Run analysis' })
  await expect(runButton).toBeDisabled()
  await runButton.hover({ force: true })
  await expect(page.getByText('This dataset has duplicate unit ids — select the date column first')).toBeVisible()

  const dateColSelect = page.getByRole('combobox', { name: 'date-column-select' })
  await dateColSelect.click()
  await page.getByTitle('event_date', { exact: true }).click()

  await expect(runButton).toBeEnabled()
  await runButton.click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })
})

// Item 2: dataZoom slider (Superset-style) on the continuous-metric
// distribution chart. A real drag on the canvas-drawn handle is too
// pixel-fragile to simulate reliably — DistributionChart.tsx exposes the
// live echarts instance on window specifically so a test can dispatchAction
// a dataZoom (the same action ECharts itself dispatches on a real
// drag/wheel) and read the result back, which is a more direct check of
// "does the wiring work" than guessing handle coordinates.
test('distribution chart has a dataZoom slider that changes the axis range', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `analyze_zoom_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()
  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await expect(page.getByText('Distribution: control vs treatment')).toBeVisible()

  // Two dataZoom components configured (slider + inside), both tied to the
  // histogram's and the ECDF's x axes — confirms the slider is actually
  // present in the chart, not just a canvas with no zoom wired up.
  const dataZoomCount = await page.evaluate(
    () => (window as unknown as { __abkitDistributionChart: { getOption: () => { dataZoom: unknown[] } } })
      .__abkitDistributionChart.getOption().dataZoom.length,
  )
  expect(dataZoomCount).toBe(2)

  const zoomState = page.getByTestId('distribution-zoom-range')
  await expect(zoomState).toHaveAttribute('data-start', '0')
  await expect(zoomState).toHaveAttribute('data-end', '100')

  await page.evaluate(() => {
    ;(window as unknown as { __abkitDistributionChart: { dispatchAction: (a: object) => void } })
      .__abkitDistributionChart.dispatchAction({ type: 'dataZoom', start: 20, end: 70 })
  })
  await expect(zoomState).toHaveAttribute('data-start', '20')
  await expect(zoomState).toHaveAttribute('data-end', '70')

  // Toggling the P99 clip control resets the zoom back to full range,
  // rather than reapplying a stale window to the new (full) axis extent.
  const fullRangeToggle = page.getByText('Full range', { exact: true })
  if (await fullRangeToggle.isVisible()) {
    await fullRangeToggle.click()
    await expect(zoomState).toHaveAttribute('data-start', '0')
    await expect(zoomState).toHaveAttribute('data-end', '100')
  }
})
