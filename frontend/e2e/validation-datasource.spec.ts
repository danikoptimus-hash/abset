import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment, uploadDataset } from './helpers'

// UX package, Validation п.C: the pre-design dataset is auto-selected so
// Run Validation is ready immediately, no forced manual upload.
test('validation auto-selects the experiment design data', async ({ page, request }) => {
  const name = `val_auto_e2e_${Date.now()}`
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto('/validation')
  await page.getByRole('combobox', { name: 'validation-experiment-select' }).click()
  await page.getByRole('combobox', { name: 'validation-experiment-select' }).fill(name)
  await page.getByTitle(name).click()

  await expect(page.getByText('From experiment design')).toBeVisible()
  await expect(page.getByText(/data\.csv/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run Validation' })).toBeEnabled()
})

test('"Use different data" reveals upload, and an incompatible file is rejected with the missing columns', async ({
  page,
  request,
}) => {
  const name = `val_incompat_e2e_${Date.now()}`
  await seedExperiment(request, name)

  // Uploaded via the API up front, before any page load: DatasetSelect's
  // query is cached per mount and isn't invalidated by an out-of-band API
  // call made later in the browser context's lifetime.
  const csv = 'some_other_column\n' + Array.from({ length: 50 }, (_, i) => `${i}`).join('\n')
  const incompatibleFilename = `incompatible_${Date.now()}.csv`
  await uploadDataset(request, csv, incompatibleFilename)

  await loginViaUi(page)

  await page.goto('/validation')
  await page.getByRole('combobox', { name: 'validation-experiment-select' }).click()
  await page.getByRole('combobox', { name: 'validation-experiment-select' }).fill(name)
  await page.getByTitle(name).click()
  await expect(page.getByText('From experiment design')).toBeVisible()

  await page.getByRole('button', { name: 'Use different data' }).click()

  const datasetSelect = page.getByRole('combobox', { name: 'validation-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(incompatibleFilename)
  await page.getByTitle(incompatibleFilename).click()

  await expect(page.getByText(/missing columns required by the experiment's design/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run Validation' })).toBeDisabled()

  await page.getByRole('button', { name: 'Reset to design data' }).click()
  await expect(page.getByText('From experiment design')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run Validation' })).toBeEnabled()
})

test('draft experiment is selectable, and n_sims below the minimum shows a validation error', async ({ page, request }) => {
  const name = `val_draft_e2e_${Date.now()}`
  // seedExperiment never publishes — the experiment stays draft, visible to
  // its own owner (UX package, Validation п.3.1: drafts must be selectable).
  await seedExperiment(request, name)
  await loginViaUi(page)

  await page.goto('/validation')
  const experimentSelect = page.getByRole('combobox', { name: 'validation-experiment-select' })
  await experimentSelect.click()
  await experimentSelect.fill(name)
  await page.getByTitle(name).click()
  await expect(page.getByText('From experiment design')).toBeVisible()

  await page.getByRole('spinbutton').first().fill('10')
  await expect(page.getByText('Enter at least 100 simulations.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run Validation' })).toBeDisabled()
})

test('Run Validation is disabled with a tooltip when no experiment is selected', async ({ page, request }) => {
  await seedExperiment(request, `val_notool_e2e_${Date.now()}`)
  await loginViaUi(page)

  await page.goto('/validation')
  const runButton = page.getByRole('button', { name: 'Run Validation' })
  await expect(runButton).toBeDisabled()
  await runButton.hover({ force: true })
  await expect(page.getByText('Select an experiment first')).toBeVisible()
})
