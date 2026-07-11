import { test, expect } from '@playwright/test'
import { loginViaUi, uploadDataset } from './helpers'

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

// Zero-inflated continuous metric (~79% exact zeros, a right-skewed positive
// tail with a real P99 outlier) — the exact shape "Positive only" exists
// for, and the same dataset also exercises the Clipped-at-P99 regression
// fix (item 4) since there's a real threshold to clip to.
function zeroInflatedValue(): string {
  const isZero = Math.random() < 0.79
  const value = isZero ? 0 : -Math.log(1 - Math.random()) * 500
  return value.toFixed(2)
}

function csvForIds(ids: string[], column: string, valueFn: () => string): string {
  return [`user_id,${column}`, ...ids.map((id) => `${id},${valueFn()}`)].join('\n')
}

async function pollJob(request: import('@playwright/test').APIRequestContext, jobId: string) {
  for (let i = 0; i < 100; i++) {
    const resp = await request.get(`${API_BASE}/jobs/${jobId}`)
    const job = await resp.json()
    if (job.status === 'completed') return job
    if (job.status === 'failed') throw new Error(`job failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 150))
  }
  throw new Error(`job ${jobId} did not finish in time`)
}

test.describe('Positive-only distribution mode + Clipped-at-P99 axis fix', () => {
  test('Positive only excludes the zero-mass jump and shows the disclosure badge; Clipped at P99 axis matches the threshold', async ({
    page,
    request,
  }) => {
    test.setTimeout(60_000)
    const name = `zero_inflated_e2e_${Date.now()}`
    const ids = Array.from({ length: 2000 }, (_, i) => `zi_${name}_${i}`)

    const loginResp = await request.post(`${API_BASE}/auth/login`, {
      data: { email: 'admin@e2e.test', password: 'e2epass123' },
    })
    if (!loginResp.ok()) throw new Error(`login failed: ${loginResp.status()}`)

    const designDataset = await uploadDataset(
      request, csvForIds(ids, 'monetary', zeroInflatedValue), `zero_design_${Date.now()}.csv`,
    )
    const designResp = await request.post(`${API_BASE}/design`, {
      data: {
        config: {
          name, unit_col: 'user_id', groups: { control: 0.5, treatment: 0.5 },
          metrics: [{ name: 'monetary', type: 'continuous', role: 'primary' }],
          sample_size: 2000, split_method: 'simple', isolation: 'off',
        },
        dataset_id: designDataset.id,
      },
    })
    if (!designResp.ok()) throw new Error(`design submit failed: ${designResp.status()}`)
    await pollJob(request, (await designResp.json()).job_id)

    // Same ids as design (assignments are keyed by them) — new zero-inflated
    // values for the post-period.
    const postFilename = `zero_post_${Date.now()}.csv`
    await uploadDataset(request, csvForIds(ids, 'monetary', zeroInflatedValue), postFilename)

    await loginViaUi(page)
    await page.goto(`/experiments/${name}`)
    await page.getByRole('tab', { name: 'Analysis' }).click()

    const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
    await datasetSelect.click()
    await datasetSelect.fill(postFilename)
    await page.getByTitle(postFilename).click()
    await expect(page.getByText(new RegExp(`Data ready: ${postFilename.replace('.', '\\.')}`))).toBeVisible()

    await page.getByRole('button', { name: 'Run analysis' }).click()
    await expect(
      page.getByText(/significant positive|significant negative|no effect detected/).first(),
    ).toBeVisible({ timeout: 20_000 })

    await expect(page.getByText('Distribution: control vs treatment')).toBeVisible()

    // --- Item 4 regression: Clipped at P99 axis must match the threshold ---
    const clippedMax = await page.evaluate(() => {
      const inst = (window as unknown as {
        __abkitDistributionChart: { getOption: () => { xAxis: { max?: number }[] } }
      }).__abkitDistributionChart
      return inst.getOption().xAxis[1].max
    })
    const footnote = await page.getByText(/axis is clipped at the 99th percentile/).textContent()
    const thresholdMatch = footnote?.match(/percentile \(([\d.]+)\)/)
    expect(thresholdMatch).toBeTruthy()
    const threshold = Number(thresholdMatch![1])
    expect(clippedMax).toBeCloseTo(threshold, 2)

    // --- Item 1: Positive only ---
    await page.getByText('Positive only', { exact: true }).click()
    await expect(
      page.getByText(/Zeros excluded for display: control \d+\.\d%, treatment \d+\.\d%/),
    ).toBeVisible()
    await expect(page.getByText(/Statistical results are computed on ALL data/)).toBeVisible()

    const firstEcdfPoint = await page.evaluate(() => {
      const inst = (window as unknown as {
        __abkitDistributionChart: { getOption: () => { series: { data: [number, number][] }[] } }
      }).__abkitDistributionChart
      // series order: [control bars, treatment bars, control ECDF line, treatment ECDF line]
      return inst.getOption().series[2].data[0]
    })
    // In the unfiltered ECDF, the first (smallest-value) point sits at the
    // zero-mass jump (~0.79 cumulative fraction, all the zeros at once).
    // With zeros excluded, the smallest point must be a small positive
    // value with a LOW cumulative fraction (close to 1/n_positive), not ~0.8.
    expect(firstEcdfPoint[0]).toBeGreaterThan(0)
    expect(firstEcdfPoint[1]).toBeLessThan(0.3)
  })

  test('metric with no zeros hides the Positive only option', async ({ page, request }) => {
    test.setTimeout(60_000)
    const name = `no_zeros_e2e_${Date.now()}`
    const ids = Array.from({ length: 500 }, (_, i) => `nz_${name}_${i}`)

    const loginResp = await request.post(`${API_BASE}/auth/login`, {
      data: { email: 'admin@e2e.test', password: 'e2epass123' },
    })
    if (!loginResp.ok()) throw new Error(`login failed: ${loginResp.status()}`)

    let i = 0
    const dataset = await uploadDataset(
      request, csvForIds(ids, 'revenue', () => (100 + ((i++) % 20)).toFixed(2)), `no_zero_design_${Date.now()}.csv`,
    )
    const designResp = await request.post(`${API_BASE}/design`, {
      data: {
        config: {
          name, unit_col: 'user_id', groups: { control: 0.5, treatment: 0.5 },
          metrics: [{ name: 'revenue', type: 'continuous', role: 'primary' }],
          sample_size: 500, split_method: 'simple', isolation: 'off',
        },
        dataset_id: dataset.id,
      },
    })
    if (!designResp.ok()) throw new Error(`design submit failed: ${designResp.status()}`)
    await pollJob(request, (await designResp.json()).job_id)

    let j = 0
    const postFilename = `no_zero_post_${Date.now()}.csv`
    await uploadDataset(
      request, csvForIds(ids, 'revenue', () => (110 + ((j++) % 20)).toFixed(2)), postFilename,
    )

    await loginViaUi(page)
    await page.goto(`/experiments/${name}`)
    await page.getByRole('tab', { name: 'Analysis' }).click()

    const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
    await datasetSelect.click()
    await datasetSelect.fill(postFilename)
    await page.getByTitle(postFilename).click()
    await expect(page.getByText(new RegExp(`Data ready: ${postFilename.replace('.', '\\.')}`))).toBeVisible()

    await page.getByRole('button', { name: 'Run analysis' }).click()
    await expect(
      page.getByText(/significant positive|significant negative|no effect detected/).first(),
    ).toBeVisible({ timeout: 20_000 })

    await expect(page.getByText('Distribution: control vs treatment')).toBeVisible()
    await expect(page.getByText('Positive only', { exact: true })).not.toBeVisible()
  })
})
