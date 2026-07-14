import { test, expect } from '@playwright/test'
import { loginViaUi } from './helpers'

// 1x1 valid PNG (Pillow must accept it — the wizard's client-side check
// only looks at File.type, but Step4Review's submit uploads real bytes
// through abkit/flow_images.py's content-sniffing/re-save, which requires
// genuinely decodable image data).
const TEST_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='

// FRONTEND.md §7 R5: "Playwright: e2e создание теста на демо-данных ->
// страница теста -> publish -> edit блока «Гипотеза»."
test('create experiment via wizard on demo data, then publish and edit hypothesis', async ({ page }) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await expect(page).toHaveURL(/\/experiments\/new$/)

  // Step 1: demo data
  await page.getByRole('button', { name: 'Demo Data' }).click()
  await expect(page.getByText(/Data loaded: 5000 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 2: experiment name (demo data already pre-fills groups/metrics)
  const expName = `wizard_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 3: parameters — isolation=off (robust to repeated e2e runs against
  // the same DB: demo data is deterministic, seed=0 -> the same 5000 users
  // every time; the default "exclude" would exclude users already occupied
  // by experiments from previous runs, leaving 0 candidates).
  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 4: run
  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  // Experiment page: lands on the Design tab, configuration and MDE table visible
  await expect(page.getByRole('tab', { name: 'Design', selected: true })).toBeVisible()
  await expect(page.getByText('MDE Table')).toBeVisible()

  // Publish — click the Draft/Published status badge itself (UX package,
  // section 1.1: it's both indicator and toggle, no separate button anymore)
  const draftBadge = page.getByText('draft', { exact: true })
  await expect(draftBadge).toBeVisible()
  await draftBadge.click()
  await expect(page.getByText('published', { exact: true })).toBeVisible()

  // Edit -> change the "Hypothesis" block -> Save
  await page.getByRole('button', { name: 'Edit' }).click()
  const hypothesisTextarea = page.locator('textarea').first()
  await hypothesisTextarea.fill('New hypothesis from the e2e test')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Saved')).toBeVisible()
  // Wait for edit mode to fully tear down (textarea unmounted) before
  // checking the read-only render — otherwise there's a brief window where
  // both exist and a plain getByText match is ambiguous (strict mode).
  await expect(page.locator('textarea')).toHaveCount(0)
  await expect(page.getByText('New hypothesis from the e2e test')).toBeVisible()
})

// 5-item follow-up п.14: the wizard's optional Hypothesis field (step 2,
// below the name field) saves into the experiment's existing Hypothesis
// block on design — visible immediately on the experiment page, no manual
// edit needed.
test('hypothesis entered in the wizard is saved into the experiment\'s Hypothesis block', async ({ page }) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await page.getByRole('button', { name: 'Demo Data' }).click()
  await expect(page.getByText(/Data loaded: 5000 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  const expName = `wizard_hypothesis_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.getByLabel('Hypothesis').fill('If we change the checkout button color, conversion will increase.')
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  await expect(
    page.getByText('If we change the checkout button color, conversion will increase.'),
  ).toBeVisible()
})

// Item 1 (critical units bug): the wizard's absolute-MDE box for a binary
// metric now shows/accepts PERCENTAGE POINTS (not a raw fraction) — typing
// "5" means 5pp, not 500 percentage points. Demo data's "clicks" binary
// metric has a deterministic baseline (seed=0, n=5000) of exactly 11.74%,
// so the pp hint text and the derived relative-MDE preview can be asserted
// on precise numbers, not just "some sane-looking value".
test('absolute MDE for a binary metric uses percentage points, not raw fractions', async ({ page }) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await page.getByRole('button', { name: 'Demo Data' }).click()
  await expect(page.getByText(/Data loaded: 5000 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  const expName = `wizard_mde_units_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.getByRole('button', { name: 'Next' }).click()

  // Step 3: switch to absolute-MDE mode on the binary "clicks" metric.
  await page.getByText('Set a target absolute MDE').click()
  // The Select's placeholder is rendered text, not a real `placeholder`
  // attribute (getByPlaceholder can't find it) — and clicking that text
  // node directly gets intercepted by the combobox's own readonly input
  // sitting on top of it, so click the select's wrapper instead.
  await page.locator('.ant-select', { hasText: 'Metric to set the absolute MDE for' }).click()
  await page.getByTitle('clicks', { exact: true }).click()

  // Baseline shown as a percentage (11.7%), with an explicit "1 pp ="
  // worked example — not the old bare "0.1174" a user could easily misread
  // the input scale from.
  await expect(page.getByText('1 pp = conversion 11.7% → 12.7%')).toBeVisible()

  // The input itself carries a "pp" suffix and defaults to 0 — typing "5"
  // must mean 5 percentage points (0.05 absolute), not 5.0 (500pp).
  await expect(page.getByText('pp', { exact: true })).toBeVisible()
  const mdeInput = page.getByRole('spinbutton').first()
  await mdeInput.fill('5')

  // relFromAbs = 0.05 / 0.1174 ≈ 42.6% — if "5" were still being read as a
  // raw fraction (5.0, the pre-fix bug), this would instead show ~4260%
  // relative MDE.
  await expect(page.getByText('≈ 42.6% relative MDE at the current mean 11.7%')).toBeVisible()

  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  // A correctly-scaled 5pp absolute MDE on "clicks" must produce a
  // plausible sample size for THAT metric, with no units-mistake warning
  // attached to it (item 1.4's guard is a backstop for genuine mistakes,
  // not a false positive on legitimate input). Demo data also configures
  // "revenue" and "conv_rate" as secondary metrics — config.mde is one
  // scalar shared by every metric (abkit/experiment.py), so the ~42.6%
  // relative MDE derived from clicks' baseline is a huge effect size for
  // revenue's very different natural scale, correctly triggering ITS OWN
  // guard warning ("revenue: Calculated sample size..."); that's expected
  // here, not something this test is about, so the assertion is scoped to
  // clicks specifically rather than "no warning anywhere on the page".
  await expect(page.getByText('MDE Table')).toBeVisible()
  await expect(page.getByText(/clicks:.*implausibly small/)).not.toBeVisible()
})

// Stage 3: optional per-group "what does this variant show/do?" description,
// entered in the wizard's Groups & Metrics step, shown on the Design tab and
// in design_report.html — editable only via Redesign afterwards.
test('group descriptions entered in the wizard show up on the Design tab and in the design report', async ({
  page,
}) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await page.getByRole('button', { name: 'Demo Data' }).click()
  await expect(page.getByText(/Data loaded: 5000 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  const expName = `wizard_groupdesc_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)

  const descriptionInputs = page.getByPlaceholder('What does this variant show/do? (optional)')
  await expect(descriptionInputs).toHaveCount(2) // demo data prefills control/treatment
  await descriptionInputs.nth(0).fill('Existing checkout flow')
  await descriptionInputs.nth(1).fill('New one-click checkout')
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  await expect(page.getByText('Existing checkout flow')).toBeVisible()
  await expect(page.getByText('New one-click checkout')).toBeVisible()

  // AntD's <Button href=... target="_blank"> renders an <a> (role "link"),
  // not role "button" — despite looking like a button.
  const [reportPage] = await Promise.all([
    page.context().waitForEvent('page'),
    page.getByRole('link', { name: 'View report' }).click(),
  ])
  await reportPage.waitForLoadState()
  await expect(reportPage.getByText('Existing checkout flow')).toBeVisible()
  await expect(reportPage.getByText('New one-click checkout')).toBeVisible()
  await reportPage.close()
})

// Stage 4: wizard with 2 groups -> upload 2 images to one variant flow
// column -> reorder them by drag -> lightbox on click -> design report
// embeds them; a SEPARATE experiment with no images shows no section at all
// (absent, not empty) on both the Design tab and the report.
test('wizard: upload 2 flow images, reorder, lightbox, and the design report embeds them', async ({ page }) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await page.getByRole('button', { name: 'Demo Data' }).click()
  await expect(page.getByText(/Data loaded: 5000 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  const expName = `wizard_flowimg_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)

  await expect(page.getByText('Variant flows (optional)')).toBeVisible()
  const controlColumn = page.getByTestId('flow-column-0')
  const fileChooserPromise = page.waitForEvent('filechooser')
  await controlColumn.getByText('Drag images here, or click to choose').click()
  const fileChooser = await fileChooserPromise
  await fileChooser.setFiles([
    { name: 'shot1.png', mimeType: 'image/png', buffer: Buffer.from(TEST_PNG_BASE64, 'base64') },
    { name: 'shot2.png', mimeType: 'image/png', buffer: Buffer.from(TEST_PNG_BASE64, 'base64') },
  ])

  const thumbs = controlColumn.locator('[data-testid^="flow-thumb-"]')
  await expect(thumbs).toHaveCount(2)
  const idsBefore = await thumbs.evaluateAll((els) => els.map((el) => el.getAttribute('data-testid')))

  // Drag the first thumbnail onto the second one's position — dnd-kit's
  // PointerSensor (activationConstraint distance: 8) reacts to real
  // mouse move/down/up, same technique as any HTML5-drag-free dnd-kit e2e.
  const first = thumbs.nth(0)
  const second = thumbs.nth(1)
  const firstBox = (await first.boundingBox())!
  const secondBox = (await second.boundingBox())!
  await page.mouse.move(firstBox.x + firstBox.width / 2, firstBox.y + firstBox.height / 2)
  await page.mouse.down()
  await page.mouse.move(secondBox.x + secondBox.width / 2, secondBox.y + secondBox.height / 2, { steps: 10 })
  await page.mouse.up()

  const idsAfter = await thumbs.evaluateAll((els) => els.map((el) => el.getAttribute('data-testid')))
  expect(idsAfter).toEqual([idsBefore[1], idsBefore[0]])

  await page.getByRole('button', { name: 'Next' }).click()
  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })

  await expect(page.getByRole('heading', { name: 'Variant flows' })).toBeVisible({ timeout: 10_000 })

  // Lightbox on the Design tab's read-only display (no dnd-kit sortable
  // context here, unlike the wizard's editable thumbnails above — a plain
  // antd Image click opens the full-size preview, a dedicated
  // <img class="ant-image-preview-img"> rendered into a portal).
  await page.locator('.ant-image').first().click()
  await expect(page.locator('.ant-image-preview-img')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.ant-image-preview-img')).not.toBeVisible()

  const [reportPage] = await Promise.all([
    page.context().waitForEvent('page'),
    page.getByRole('link', { name: 'View report' }).click(),
  ])
  await reportPage.waitForLoadState()
  await expect(reportPage.locator('#section-flows')).toBeVisible()
  await expect(reportPage.locator('#section-flows img')).toHaveCount(2)
  await reportPage.close()
})

test('experiment with no flow images shows no Variant flows section, on the Design tab or the report', async ({
  page,
}) => {
  test.setTimeout(60_000)
  await loginViaUi(page)

  await page.getByRole('button', { name: 'Create A/B Test' }).click()
  await page.getByRole('button', { name: 'Demo Data' }).click()
  await expect(page.getByText(/Data loaded: 5000 rows/)).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: 'Next' }).click()

  const expName = `wizard_noflowimg_e2e_${Date.now()}`
  await page.getByPlaceholder('Experiment name').fill(expName)
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByText(/exclude — exclude participants/).click()
  await page.getByText(/off — exclude no one/).click()
  await page.getByRole('button', { name: 'Next' }).click()

  await page.getByRole('button', { name: 'Design' }).click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${expName}$`), { timeout: 20_000 })
  await expect(page.getByText('MDE Table')).toBeVisible()

  await expect(page.getByRole('heading', { name: 'Variant flows' })).toHaveCount(0)

  const [reportPage] = await Promise.all([
    page.context().waitForEvent('page'),
    page.getByRole('link', { name: 'View report' }).click(),
  ])
  await reportPage.waitForLoadState()
  await expect(reportPage.locator('#section-flows')).toHaveCount(0)
  await reportPage.close()
})
