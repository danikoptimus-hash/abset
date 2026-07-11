import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

// 5-part package pt.3: Redesign for 'designed'-status experiments — the
// wizard opens pre-filled, submitting replaces the split in place (same
// experiment), and the action disappears entirely once running.

test('Redesign replaces the split in place; the Redesign action is gone once running', async ({
  page,
  request,
}) => {
  const name = `redesign_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByText('Redesign', { exact: true }).click()

  const confirmDialog = page.getByRole('dialog').filter({ hasText: 'Redesign this experiment?' })
  await expect(confirmDialog).toBeVisible()
  await expect(confirmDialog.getByText(/Analyses already run against the old split will be deleted/)).toBeVisible()
  await confirmDialog.getByRole('button', { name: 'Continue' }).click()

  await expect(page).toHaveURL(new RegExp(`/experiments/${name}/redesign$`))
  await expect(page.getByText(`Redesigning "${name}"`)).toBeVisible()

  // Pre-filled — dataset carried over from the existing config, already
  // selected on step 0 (Data).
  await expect(page.getByText('Data loaded:')).toBeVisible()

  // Step 1 (Groups & Metrics) is where the name field lives — pre-filled
  // and locked, since a redesign can't rename the experiment.
  await page.getByRole('button', { name: 'Next' }).click()
  await expect(page.getByPlaceholder('Experiment name')).toHaveValue(name)
  await expect(page.getByPlaceholder('Experiment name')).toBeDisabled()

  await page.getByRole('button', { name: 'Next' }).click()
  await page.getByRole('button', { name: 'Next' }).click()
  await page.getByRole('button', { name: 'Redesign' }).click()

  await expect(page).toHaveURL(new RegExp(`/experiments/${name}$`), { timeout: 15_000 })
  await expect(page.getByRole('heading', { name })).toBeVisible()

  // Move to running — the Redesign menu item must disappear entirely (not
  // merely disabled), per pt.3.4.
  await page.getByText('designed', { exact: true }).click()
  await page.getByText('Move to running').click()
  await expect(page.getByText('running', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'More actions' }).click()
  await expect(page.getByText('Redesign', { exact: true })).not.toBeVisible()
})

// Stage 3, item 3.4: group descriptions are only editable through Redesign —
// this confirms the wizard actually reads them back from the existing
// config (types.ts::wizardStateFromConfig -> groupsFromApi) rather than
// just carrying name/proportion, as `redesign.spec.ts`'s main test above
// only checks.
test('Redesign preloads existing group descriptions into the wizard', async ({ page, request }) => {
  const name = `redesign_groupdesc_e2e_${Date.now()}`
  const email = 'admin@e2e.test'
  const password = 'e2epass123'
  const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000/api/v1'

  const loginResp = await request.post(`${API_BASE}/auth/login`, { data: { email, password } })
  if (!loginResp.ok()) throw new Error(`login failed: ${loginResp.status()}`)

  const lines = ['user_id,revenue'].concat(
    Array.from({ length: 200 }, (_, i) => `u_${name}_${i},${100 + (i % 10)}`),
  )
  const uploadResp = await request.post(`${API_BASE}/datasets`, {
    multipart: {
      kind: 'pre_design',
      file: { name: 'data.csv', mimeType: 'text/csv', buffer: Buffer.from(lines.join('\n')) },
    },
  })
  if (!uploadResp.ok()) throw new Error(`upload failed: ${uploadResp.status()}`)
  const dataset = await uploadResp.json()

  const designResp = await request.post(`${API_BASE}/design`, {
    data: {
      config: {
        name,
        unit_col: 'user_id',
        groups: { control: 0.5, treatment: 0.5 },
        group_descriptions: { control: 'Existing checkout flow', treatment: 'New one-click checkout' },
        metrics: [{ name: 'revenue', type: 'continuous', role: 'primary' }],
        sample_size: 200,
        split_method: 'simple',
        isolation: 'off',
      },
      dataset_id: dataset.id,
    },
  })
  if (!designResp.ok()) throw new Error(`design submit failed: ${designResp.status()}`)
  const { job_id: jobId } = await designResp.json()
  for (let i = 0; i < 100; i++) {
    const jobResp = await request.get(`${API_BASE}/jobs/${jobId}`)
    const job = await jobResp.json()
    if (job.status === 'completed') break
    if (job.status === 'failed') throw new Error(`design job failed: ${job.error}`)
    await new Promise((r) => setTimeout(r, 100))
  }

  await loginViaUi(page)
  await page.goto(`/experiments/${name}`)

  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByText('Redesign', { exact: true }).click()
  await page.getByRole('dialog').filter({ hasText: 'Redesign this experiment?' }).getByRole('button', { name: 'Continue' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${name}/redesign$`))

  await page.getByRole('button', { name: 'Next' }).click() // Step 1 (Groups & Metrics)
  const descriptionInputs = page.getByPlaceholder('What does this variant show/do? (optional)')
  await expect(descriptionInputs.nth(0)).toHaveValue('Existing checkout flow')
  await expect(descriptionInputs.nth(1)).toHaveValue('New one-click checkout')
})
