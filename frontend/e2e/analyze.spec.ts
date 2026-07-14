import { test, expect } from '@playwright/test'
import { clickSelectOption, loginViaUi, seedExperiment, seedTwoMetricExperiment, uploadDataset } from './helpers'

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
  // Stage 2 item 2.2: same "Created ... · Started ..." line as the header,
  // next to the "Analyzed ... (run #N)" line on Results. The header renders
  // its own copy too (unaffected by which tab is active), so two matches
  // exist on screen — .last() is the Results tab's (renders after the
  // header in DOM order).
  await expect(page.getByText(/Created \w+ \d+/).last()).toBeVisible()

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

// Item 3 (consolidated package, multi-select methods): retired the old
// "compare_methods=True can accidentally duplicate the designed chain"
// scenario this test used to cover — that could only happen because the
// FIXED standard compare set (compare_methods_chains()) sometimes repeated
// the designed chain verbatim, and the new per-metric multi-select can't
// select the same method id twice, so a real duplicate can no longer arise
// through the UI at all (dedupeDesignedDuplicates()/detailed_rows()' dedup
// stays in place defensively, still covered by Python-level tests, e.g.
// tests/test_experiment_analyze.py::
// test_detailed_rows_includes_all_comparisons_sorted_by_metric_then_method).
// Its replacement — "selecting N methods produces N rows, the primary is
// bolded" — is the method-selector test further down this file.

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
  await page.waitForFunction(
    () => !!(window as unknown as { __abkitDistributionChart?: unknown }).__abkitDistributionChart,
    { timeout: 10_000 },
  )

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

// Stage 1 (chart tooltips), item 1.4: hovering the forest plot and the ECDF
// line shows a tooltip with concrete numbers. Same technique as the dataZoom
// test above — dispatchAction a 'showTip' (the same action ECharts itself
// dispatches on a real mouseover) on the exposed live instance instead of
// simulating pixel-precise mouse coordinates over a canvas, then read the
// resulting tooltip DOM content back.
test('hovering the forest plot and the ECDF shows a tooltip with numbers', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `analyze_tooltip_e2e_${Date.now()}`
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
  await expect(page.getByRole('heading', { name: 'Forest plot' })).toBeVisible()

  await page.waitForFunction(() => !!(window as unknown as { __abkitForestChart?: unknown }).__abkitForestChart)
  await page.evaluate(() => {
    ;(window as unknown as { __abkitForestChart: { dispatchAction: (a: object) => void } })
      .__abkitForestChart.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: 0 })
  })
  await expect(page.getByText(/Effect: -?\d/)).toBeVisible()
  await expect(page.getByText(/95% CI: \[/)).toBeVisible()
  await expect(page.getByText(/p-value: /)).toBeVisible()

  await expect(page.getByText('Distribution: control vs treatment')).toBeVisible()
  await page.waitForFunction(
    () => !!(window as unknown as { __abkitDistributionChart?: unknown }).__abkitDistributionChart,
  )
  await page.evaluate(() => {
    // Series order (ContinuousDistributionChart): 0/1 = histogram bars
    // (control/treatment), 2/3 = ECDF lines (control/treatment).
    ;(window as unknown as { __abkitDistributionChart: { dispatchAction: (a: object) => void } })
      .__abkitDistributionChart.dispatchAction({ type: 'showTip', seriesIndex: 2, dataIndex: 0 })
  })
  await expect(page.getByText(/cumulative=\d/)).toBeVisible()
})

// Item 3 (consolidated package, multi-select methods): the per-metric
// method selector on the Analysis tab is type-aware (no Z-test of
// proportions for a continuous metric) and pre_col-aware (no CUPED option
// without a pre-period column — seedExperiment's revenue metric has none).
// Deselecting the recommended default (leaving only Mann-Whitney selected)
// auto-promotes it to primary/designed: the Results row is bolded
// (rowClassName, tested generically elsewhere) and carries an explicit
// "manually selected" tag.
test('Analysis method selector hides inapplicable methods and picking a non-default one is reflected in Results', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `analyze_method_select_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const methodSelect = page.getByRole('combobox', { name: 'method-select-revenue' })
  await expect(methodSelect).toBeVisible()
  await methodSelect.click()
  // AntD Select keeps a hidden a11y-only role="option" node around even when
  // closed (mirroring the current selection) — page.getByRole('option')
  // matches that too, so scope to the actual dropdown list's option content
  // (same convention other e2e specs use for AntD Select: getByTitle on the
  // option, since each option row carries a title attribute with its label).
  const optionsLocator = page.locator('.ant-select-item-option-content')
  await expect(optionsLocator.first()).toBeVisible()

  const optionTexts = await optionsLocator.allTextContents()
  expect(optionTexts.some((t) => /Z-test/.test(t))).toBe(false)
  expect(optionTexts.some((t) => /CUPED/.test(t))).toBe(false)
  expect(optionTexts.some((t) => /Mann-Whitney/.test(t))).toBe(true)
  expect(optionTexts.some((t) => /Welch t-test.*recommended/.test(t))).toBe(true)

  // Add Mann-Whitney (mode="multiple" keeps the dropdown open), then toggle
  // the default Welch selection back off — left with only Mann-Whitney
  // selected, it becomes the sole (and thus primary) method.
  await clickSelectOption(page, 'Mann-Whitney (Hodges-Lehmann)')
  await clickSelectOption(page, 'Welch t-test (recommended)')
  await expect(
    page.getByText(/differs from the designed method — power was calculated for Welch t-test/),
  ).toBeVisible()

  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await page.getByRole('tab', { name: 'Results' }).click()
  const designedRow = page.locator('tr.detailed-results-designed-row').filter({ hasText: 'revenue' })
  await expect(designedRow.getByText('Mann-Whitney (Hodges-Lehmann)')).toBeVisible()
  await expect(designedRow.getByText('manually selected')).toBeVisible()
})

// Item 3.4: selecting three methods for a metric produces three rows in the
// Detailed Results Table, with the designed/primary one bolded — replaces
// the retired "Compare alternative methods" checkbox (job-failure-handling.
// spec.ts used to cover the equivalent "extra rows appear" case with it).
test('selecting three methods produces three result rows with the primary bolded', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `analyze_multiselect3_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()

  const methodSelect = page.getByRole('combobox', { name: 'method-select-revenue' })
  await methodSelect.click()
  await expect(page.locator('.ant-select-item-option-content').first()).toBeVisible()
  // Welch (recommended) is already selected by default — add two more,
  // leaving the default as primary.
  await clickSelectOption(page, 'Mann-Whitney (Hodges-Lehmann)')
  await clickSelectOption(page, 'Bootstrap (bca)')
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByRole('cell', { name: 'Welch t-test', exact: true })).toHaveCount(1)
  await expect(page.getByRole('cell', { name: 'Mann-Whitney (Hodges-Lehmann)', exact: true })).toHaveCount(1)
  await expect(page.getByRole('cell', { name: 'Bootstrap (bca)', exact: true })).toHaveCount(1)
  const designedRow = page.locator('tr.detailed-results-designed-row').filter({ hasText: 'revenue' })
  await expect(designedRow.getByText('Welch t-test', { exact: true })).toBeVisible()
  await expect(designedRow.getByText('manually selected')).not.toBeVisible()
})

// Item 3.4: leaving just the single (recommended) default selected for a
// metric produces exactly one row — no comparison rows at all, unlike the
// old "Compare alternative methods" checkbox which defaulted to on.
test('leaving the default single method selected produces exactly one result row', async ({ page, request }) => {
  test.setTimeout(60_000)
  const name = `analyze_singleselect_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto(`/experiments/${name}`)
  await page.getByRole('tab', { name: 'Analysis' }).click()
  // Nothing touched in the method selector — default is [Welch] only.
  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  await page.getByRole('tab', { name: 'Results' }).click()
  // Scoped to the Detailed Results Table specifically (identified by its
  // "Metric" column header) — a bare 'tbody tr' matches rows in OTHER
  // tables on the Results tab too (e.g. VerdictCards render no table, but
  // other page tables do), which was over-counting.
  const detailedTable = page.locator('table').filter({ has: page.getByRole('columnheader', { name: 'Metric' }) })
  const revenueRows = detailedTable.locator('tbody tr').filter({ hasText: 'revenue' })
  await expect(revenueRows).toHaveCount(1)
  await expect(revenueRows.getByText('Welch t-test', { exact: true })).toBeVisible()
})
