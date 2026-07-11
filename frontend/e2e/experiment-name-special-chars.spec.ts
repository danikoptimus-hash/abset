import { test, expect } from '@playwright/test'
import { loginViaUi, seedExperiment, uploadDataset } from './helpers'

// Permanent regression fixture for the "experiments are addressed by name"
// bug class (CLAUDE.md, "Известный техдолг"): three separate incidents have
// come from a name containing characters that need escaping somewhere along
// the chain — samples download crashing on a non-ASCII Content-Disposition
// header (commit c0c8aea), the History tab conflating a deleted and a
// recreated same-named experiment (object_name vs object_id), and a real
// user report (ref edb716f1, root cause unrelated — a column collision in
// the analyze join, see abkit/checks.py::join_with_assignments) whose
// triage first suspected this same class. This test exercises a Cyrillic +
// colon + space name (mirroring the real report's "PA: ...") through every
// hop of the real chain: the experiments list's row link (React Router
// <Link>, not a hand-built URL), the browser's own URL encoding, nginx
// passthrough, FastAPI's path-param decoding, and analyze all the way to a
// verdict on an uploaded (not demo-generated) CSV — plus the History tab,
// which is where the object_id/object_name bug actually lived.
test('experiment with a Cyrillic name containing a colon: list navigation, analyze on a real CSV, and History all work', async ({
  page,
  request,
}) => {
  test.setTimeout(60_000)
  const name = `ПА: э2э тест ${Date.now()}`

  await seedExperiment(request, name)

  const csv =
    'user_id,revenue\n' +
    Array.from({ length: 200 }, (_, i) => `u_${name}_${i},${100 + (i % 10)}.5`).join('\n')
  const filename = `special_chars_post_${Date.now()}.csv`
  await uploadDataset(request, csv, filename)

  await loginViaUi(page)

  // Real UI path: search the list, click the row's own <Link>, rather than
  // page.goto()-ing a pre-encoded URL ourselves.
  await page.goto('/experiments')
  await page.getByPlaceholder('Search by name or tag...').fill(name)
  await page.getByRole('link', { name }).click()

  // Whatever encoding the browser chose for the ':'/Cyrillic/space in the
  // address bar, the SPA must have resolved it back to the right
  // experiment — decoding the URL's last path segment must round-trip to
  // the exact name.
  await expect(page).toHaveURL(/\/experiments\/.+/)
  const url = new URL(page.url())
  const slug = decodeURIComponent(url.pathname.replace(/^\/experiments\//, ''))
  expect(slug).toBe(name)
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()

  await page.getByRole('tab', { name: 'Analysis' }).click()
  const datasetSelect = page.getByRole('combobox', { name: 'post-period-dataset-select' })
  await datasetSelect.click()
  await datasetSelect.fill(filename)
  await page.getByTitle(filename).click()
  await expect(page.getByText(new RegExp(`Data ready: ${filename.replace('.', '\\.')}`))).toBeVisible()

  await page.getByRole('button', { name: 'Run analysis' }).click()
  await expect(
    page.getByText(/significant positive|significant negative|no effect detected/).first(),
  ).toBeVisible({ timeout: 20_000 })

  // History tab (item 15's fix): must load this experiment's own events by
  // id, not blow up or show anything from an unrelated same-named row.
  await page.getByRole('tab', { name: 'History' }).click()
  await expect(page.getByRole('cell', { name: 'experiment.create' })).toBeVisible()
})
