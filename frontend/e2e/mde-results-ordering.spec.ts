import fs from 'node:fs'
import { test, expect } from '@playwright/test'
import { clickSelectOption, loginViaUi, seedTwoMetricExperimentSecondaryDeclaredFirst } from './helpers'

// 6-part package pt.1-3: Required n per group (ceil, renamed, tooltipped) +
// actual group sizes row on the Design tab's MDE table, and primary-first
// ordering everywhere a metric list is shown. Seeded with clicks (secondary)
// declared BEFORE revenue (primary) — a primary-first config would pass
// even without the fix, so the declared order is deliberately reversed.
test('MDE table shows required-n/actual-sizes and primary metrics before secondary everywhere', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `mde_results_order_e2e_${Date.now()}`
  await seedTwoMetricExperimentSecondaryDeclaredFirst(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  // AntD Tabs keeps inactive panes mounted (just aria-hidden), so any
  // '.ant-table-tbody tr' query must be scoped to the currently active
  // panel — otherwise it also picks up rows from a tab visited earlier in
  // this test (e.g. the Design tab's MDE table still being in the DOM
  // while checking the Results tab's Detailed Results Table below).
  const activePanel = page.locator('[role="tabpanel"]:not([aria-hidden="true"])')

  // --- Design tab: items 1 and 2 ---
  await page.getByRole('tab', { name: 'Design' }).click()
  await expect(page.getByText('Required n per group', { exact: true })).toBeVisible()

  const mdeRows = activePanel.locator('.ant-table-tbody tr')
  const mdeRowTexts = await mdeRows.allTextContents()
  const mdeMetricRows = mdeRowTexts.filter((t) => t.includes('revenue') || t.includes('clicks'))
  const revenueIdx = mdeMetricRows.findIndex((t) => t.includes('revenue'))
  const clicksIdx = mdeMetricRows.findIndex((t) => t.includes('clicks'))
  expect(revenueIdx).toBeGreaterThanOrEqual(0)
  expect(clicksIdx).toBeGreaterThan(revenueIdx)
  // Role badges present for both, not just a secondary marker.
  expect(mdeMetricRows[revenueIdx]).toContain('primary')
  expect(mdeMetricRows[clicksIdx]).toContain('secondary')
  // Required n per group renders as a plain integer ("100", 200 rows/2
  // groups) — AntD's allTextContents() concatenates cells with no
  // separator, and Required n per group is the last column (no CUPED here),
  // so anchoring to end-of-string reliably targets that cell specifically
  // rather than an unrelated "...100" substring inside a decimal value
  // elsewhere in the row. Exact ceil-of-a-fraction rounding is covered
  // precisely by the backend test (a case built to actually differ from
  // round()).
  expect(mdeMetricRows[revenueIdx]).toMatch(/100$/)
  expect(mdeMetricRows[clicksIdx]).toMatch(/100$/)

  // Scoped to the single combined text node (not a bare /control \d+/,
  // which also matches the unrelated "control 50%" group-proportions line
  // in the Configuration panel above).
  await expect(page.getByText(/Actual group sizes: control \d+ · treatment \d+/)).toBeVisible()

  // --- Analysis tab: give revenue extra methods, to exercise "designed
  // first, then alternatives" ordering in the Results table below. ---
  await page.getByRole('tab', { name: 'Analysis' }).click()
  const methodSelect = page.getByRole('combobox', { name: 'method-select-revenue' })
  await methodSelect.click()
  await expect(page.locator('.ant-select-item-option-content').first()).toBeVisible()
  await clickSelectOption(page, 'Mann-Whitney (Hodges-Lehmann)')
  await clickSelectOption(page, 'Bootstrap (bca)')
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: /Generate demo post-period data/ }).click()
  await expect(page.getByText(/Demo data generated:/)).toBeVisible({ timeout: 10_000 })
  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // --- Analysis tab verdict cards: primary (revenue) before secondary
  // (clicks), even though clicks was declared first. ---
  const cardTexts = await activePanel.locator('.ant-card').allTextContents()
  const cardRevenueIdx = cardTexts.findIndex((t) => t.includes('revenue'))
  const cardClicksIdx = cardTexts.findIndex((t) => t.includes('clicks'))
  expect(cardRevenueIdx).toBeGreaterThanOrEqual(0)
  expect(cardClicksIdx).toBeGreaterThan(cardRevenueIdx)

  // --- Results tab: Detailed Results Table + CSV export ---
  await page.getByRole('tab', { name: 'Results' }).click()
  await expect(page.getByText('Detailed Results Table')).toBeVisible()

  const detailRowTexts = await activePanel.locator('.ant-table-tbody tr').allTextContents()
  const revenueRowIdxs = detailRowTexts
    .map((t, i) => (t.includes('revenue') ? i : -1))
    .filter((i) => i >= 0)
  const clicksRowIdxs = detailRowTexts
    .map((t, i) => (t.includes('clicks') ? i : -1))
    .filter((i) => i >= 0)
  expect(revenueRowIdxs.length).toBe(3) // Welch + Mann-Whitney + Bootstrap
  expect(clicksRowIdxs.length).toBe(1)
  // All revenue rows precede the clicks row.
  expect(Math.max(...revenueRowIdxs)).toBeLessThan(Math.min(...clicksRowIdxs))
  // Within revenue's rows, the designed (bolded) one — Welch t-test — comes
  // first, not alphabetically-first ("Bootstrap (bca)").
  expect(detailRowTexts[revenueRowIdxs[0]]).toContain('Welch t-test')

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export CSV' }).click()
  const download = await downloadPromise
  const csvPath = await download.path()
  const csv = fs.readFileSync(csvPath!, 'utf-8')
  const csvLines = csv.trim().split('\n')
  const csvMetricCol = csvLines.map((line) => line.split(',')[0])
  const csvRevenueIdxs = csvMetricCol.map((m, i) => (m === 'revenue' ? i : -1)).filter((i) => i >= 0)
  const csvClicksIdxs = csvMetricCol.map((m, i) => (m === 'clicks' ? i : -1)).filter((i) => i >= 0)
  expect(csvRevenueIdxs.length).toBe(3)
  expect(csvClicksIdxs.length).toBe(1)
  expect(Math.max(...csvRevenueIdxs)).toBeLessThan(Math.min(...csvClicksIdxs))
  expect(csvLines[csvRevenueIdxs[0]]).toContain('Welch t-test')
})
