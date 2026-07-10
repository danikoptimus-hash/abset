import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment } from './helpers'

// Tags for A/B tests (Superset-style dashboard tags, CLAUDE.md).

test('create a tag via Properties, see its badge in the list, filter by it, and clicking the badge filters too', async ({
  page,
  request,
}) => {
  const taggedName = `tags_e2e_tagged_${Date.now()}`
  const otherName = `tags_e2e_other_${Date.now()}`
  const tagName = `e2e-tag-${Date.now()}`
  await seedExperiment(request, taggedName)
  await seedExperiment(request, otherName)
  await loginViaUi(page)
  await page.goto('/experiments')

  // Create the tag by typing a brand-new name into the Properties modal's
  // Tags field and pressing Enter (mode="tags" — no separate "create" click).
  const row = page.getByRole('row', { name: new RegExp(taggedName) })
  await row.hover()
  await row.getByRole('button', { name: 'Edit' }).click()
  const modal = page.getByRole('dialog')
  await expect(modal.getByText('Edit Properties')).toBeVisible()

  const tagsSelect = modal.getByRole('combobox', { name: 'Tags' })
  await tagsSelect.click()
  await tagsSelect.fill(tagName)
  await page.keyboard.press('Enter')
  await modal.getByRole('button', { name: 'Save' }).click()
  await expect(modal).not.toBeVisible()

  // Badge shows in the list row, on the OTHER row it does not.
  const taggedRow = page.getByRole('row', { name: new RegExp(taggedName) })
  const otherRow = page.getByRole('row', { name: new RegExp(otherName) })
  await expect(taggedRow.getByText(tagName)).toBeVisible()
  await expect(otherRow.getByText(tagName)).not.toBeVisible()

  // Tags filter narrows the list to just the tagged experiment.
  const tagFilter = page.getByRole('combobox', { name: 'Tags filter' })
  await tagFilter.click()
  await tagFilter.fill(tagName)
  await page.getByTitle(tagName, { exact: true }).click()
  await page.keyboard.press('Escape')

  await expect(page.getByRole('row', { name: new RegExp(taggedName) })).toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(otherName) })).not.toBeVisible()

  // Clear the filter, then prove clicking the badge itself does the same
  // narrowing (UX package, Tags §3.5 — click-to-filter).
  await tagFilter.click()
  await page.keyboard.press('Escape')
  await page.reload()
  await expect(page.getByRole('row', { name: new RegExp(otherName) })).toBeVisible()

  await page.getByRole('row', { name: new RegExp(taggedName) }).getByText(tagName).click()
  await expect(page.getByRole('row', { name: new RegExp(taggedName) })).toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(otherName) })).not.toBeVisible()
})

test('clicking a tag badge on the experiment page navigates to a filtered list', async ({ page, request }) => {
  const taggedName = `tags_e2e_page_${Date.now()}`
  const otherName = `tags_e2e_page_other_${Date.now()}`
  const tagName = `e2e-page-tag-${Date.now()}`
  await seedExperiment(request, taggedName)
  await seedExperiment(request, otherName)
  await loginViaUi(page)
  await page.goto(`/experiments/${taggedName}`)

  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByText('Edit Properties').click()
  const modal = page.getByRole('dialog')
  const tagsSelect = modal.getByRole('combobox', { name: 'Tags' })
  await tagsSelect.click()
  await tagsSelect.fill(tagName)
  await page.keyboard.press('Enter')
  await modal.getByRole('button', { name: 'Save' }).click()
  await expect(modal).not.toBeVisible()

  // Badge now shows in its own row under the header (not crammed into the
  // status-badge line).
  const badge = page.getByText(tagName, { exact: true })
  await expect(badge).toBeVisible()
  await badge.click()

  await expect(page).toHaveURL(/\/experiments\?tag=/)
  await expect(page.getByRole('row', { name: new RegExp(taggedName) })).toBeVisible()
  await expect(page.getByRole('row', { name: new RegExp(otherName) })).not.toBeVisible()
})
