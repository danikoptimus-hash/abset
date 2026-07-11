# ABSet User Guide

For analysts running A/B tests day to day. If you're deploying or maintaining
ABSet itself, see [OPERATIONS.md](OPERATIONS.md) instead.

## What is ABSet

ABSet is a self-hosted A/B-testing tool that takes you from "I have a pool of
candidate users and a metric I care about" to a statistically defensible
decision: it calculates the sample size and minimum detectable effect (MDE)
you need before you start, splits users into groups (with stratification and
isolation from other running tests), runs a pipeline of statistical methods
(Welch's t-test, proportions z-test, CUPED, bootstrap, Mann-Whitney, the delta
method for ratio metrics) with multiple-testing correction, and — crucially —
lets you validate that the whole pipeline is honest on your own data via A/A
and A/B simulations before you trust a single real result from it.

## Core concepts

### Experiment lifecycle

Every experiment has two independent pieces of state:

- **Operational status** — where the experiment actually is: `designed` →
  `running` → `completed`, with `archived` as an escape hatch reachable from
  any status (and un-archivable back). This is what you set as the test
  physically progresses — designing it doesn't mean it's live yet, and
  finishing data collection doesn't mean you're done drawing conclusions.
- **Publication status** — `draft` or `published`. A published experiment is
  visible to everyone whose role allows viewing it; a draft is visible only to
  its owner, anyone explicitly granted access, and Admins. Publishing is a
  deliberate "this is ready for others to see" action, separate from whether
  the test itself has finished running.

Both are toggled from clickable badges at the top of the experiment page. The
status dropdown allows moving backward too (`completed` → `running`,
`running` → `designed`, or un-archiving to any status) — each backward move
asks you to confirm first, with a warning specific to what it means:
returning to `designed` warns that existing analyses are kept (use
**Redesign**, below, if you actually want to change the design); reopening a
`completed` test warns about peeking (re-checking significance after
extending data collection inflates the false-positive rate); un-archiving
warns you to make sure that's really the state you want. Moving forward, or
archiving from anywhere, stays frictionless — only backward moves ask.

While an experiment is still `designed` (before you've moved it to
`running`), its "⋯" menu offers **Redesign** — discard the current split,
MDE table, and split checks, and reopen the design wizard pre-filled with
the experiment's current config (dataset, groups, metrics, everything —
including the dataset, which you're free to swap for a different one). The
experiment's name can't be changed as part of a redesign (use Edit
Properties for that). Submitting replaces the split and config on the same
experiment — it isn't a new one — and deletes any analysis results already
run against the old split, since they describe a randomization that no
longer exists; the confirmation dialog tells you exactly how many will be
deleted before you proceed. Once an experiment has moved past `designed`,
Redesign disappears entirely (not just disabled) — redesigning a test after
it's started collecting data would invalidate whatever's already been
observed, so the only path from there is **Archive** and a new experiment.

### Datasets: where your data comes from

ABSet doesn't accept files directly inside the design wizard or Analyze/
Validation anymore — everything reads from a **dataset**, created once on the
[Datasets](#) page and then reused wherever you need it. A dataset is either:

- an **uploaded file** (CSV or parquet), or
- the **result of a SQL query** against a database connection your Admin has
  configured (PostgreSQL, ClickHouse, MSSQL) — pick a connection, optionally a
  schema/table from a searchable picker (which fills in
  `SELECT * FROM "schema"."table"` for you), preview the first rows, and save.
  The query result is materialized once into ABSet's own storage — deleting
  the source table afterward does not affect your dataset. Use **Refresh** on
  the dataset (Editor+) to re-run the query and pull current data later.

Both kinds show up identically in every dataset picker across the app.
`[Screenshot: Datasets page with a mix of Upload and SQL-source rows]`

Datasets are independent of any one experiment — **deleting an experiment
never deletes the datasets it used.** Only that experiment's own assignments
and analysis results go away; its datasets simply lose that one link and stay
right where they were on the Datasets page, ready to be reused for another
design. The experiment's delete confirmation spells this out explicitly.

On the Datasets page, **Bulk select** (next to **+ Dataset**) turns on a
checkbox column — select several datasets and **Delete** removes all of them
after one typed `DELETE` confirmation. Datasets already in use by an
experiment are listed with "used by: ..." right in the confirmation dialog
(deleting them is still allowed — it doesn't affect those experiments'
existing analysis results, only their "current data" link). If you don't
have permission to delete some of the selected rows (you're not their owner
or an Admin), those are skipped and reported separately — "Deleted N,
skipped M (no permission)" — instead of silently failing the whole batch.

### Tags: organizing and finding experiments

Any experiment can carry any number of **tags** — free-form labels for
product, team, feature, or whatever grouping makes sense to your
organization (the same idea as tags on a Superset dashboard). Add them from
the **Edit Properties** modal's Tags field: pick an existing tag from the
typeahead, or type a new name and press Enter to create it on the spot — no
separate "manage tags" screen needed for day-to-day use. Each tag gets a
consistent color derived from its name, so the same tag always looks the
same wherever it shows up.

Tags appear as small badges in the experiments list (a row with more than a
couple collapses the rest into a "+N" badge — hover it to see the full list)
and under the title on the experiment page. Click any tag badge — in the
list or on an experiment page — to jump straight to the list filtered to
that tag. The list also has its own **Tags** filter (select more than one to
narrow to experiments that have *all* of them, not just any), and the search
box matches tag names as well as experiment names, so typing a tag finds
every experiment tagged with it without opening the filter at all.

Only an Admin can delete a tag outright (removing it from every experiment
that had it) — anyone with edit access to a given experiment can add or
remove tags on that one experiment through its own Properties modal.

### Roles

| Role | Can do |
|---|---|
| **Viewer** | See experiments/reports they have access to, download reports |
| **Editor** | Everything a Viewer can, plus create experiments and run Design/Analyze/Validation on any experiment they can see |
| **Admin** | Everything, plus manage users, see the full audit log, and configure Database Connections |

On top of the base role, an experiment has an **owner** (its creator) and can
have additional **access-editors** granted through the Edit Properties modal —
these are the people (besides the owner and Admins) who can rename the
experiment, edit its Hypothesis/Conclusions/Decision text, change its
operational/publication status, or delete it. A draft experiment is invisible
to editors without a grant; a published one is visible to everyone unless its
owner restricted visibility to specific roles.

## Walkthrough: from data to decision

### 1. Prepare a dataset and write your hypothesis

Before opening the design wizard, get your **pre-period** data into a dataset
(see above) — one row per candidate user, with a user ID column and, ideally,
columns you'll want to stratify on and pre-period values of the metrics you
care about. The in-app help on the wizard's first step spells out exactly why
the optional columns matter: attribute columns for stratification ("recommended
— without them groups won't be balanced") and pre-period metric columns
("recommended — CUPED and an accurate MDE calculation don't work without
them"). No data on hand yet? Use **Demo Data** to generate a synthetic dataset
and try the whole flow risk-free.

Write down your hypothesis before you look at any results — the design
wizard's second step (**Groups & Metrics**) has an optional **Hypothesis**
field for exactly this, with an in-app hint on how to phrase one ("If we
change X, it will affect Y, which we will observe as a change in metric Z").
Whatever you type there is saved straight into the experiment page's
**Hypothesis** text block, so the prediction is on record before the outcome
could bias how you phrase it. You can skip it in the wizard and fill the
block in later instead — both write to the same place.

### 2. Design wizard

Four steps: **Data → Groups & Metrics → Parameters → Run**.

**Data** — choose the split mode first: **ABSet split** (the flow described
below — ABSet picks candidates, splits them, and stores assignments) or
**External split** (the split already happened in an outside system, e.g.
Firebase A/B Testing — see [External split mode](#7-external-split-mode-firebase-etc)
below for that flow instead). For ABSet split, pick the dataset from step 1
(search existing datasets or create a new one inline).

**Groups & Metrics**:
- Groups — name your arms and set their traffic split. Presets for the common
  shapes (`50/50`, `90/10`, `33/33/33`) plus a **Normalize** button if your
  numbers don't add to 100%.
- Metrics — at least one, each with:
  - **Type**: `continuous`, `binary`, or `ratio` (ratio metrics take a
    numerator/denominator column pair instead of a single column — for
    metrics like revenue-per-session where both parts vary per user).
  - **Role**: `primary` or `secondary`. The verdict and multiple-testing
    correction are computed from primary metrics; secondary metrics are
    exploratory context, not decision inputs.
  - **Pre-period column** (optional, non-ratio metrics) — the same metric's
    value before the test started. Supplying it is what enables **CUPED**
    (variance reduction using the correlation between pre- and post-period
    values) for that metric.

**Parameters**:
- **Experiment size** — pick one of: a target relative MDE, a target absolute
  MDE, a fixed sample size, or "use all available data". Relative MDE is
  expressed as a fraction of the current mean (e.g. `0.05` = detect a 5%
  lift); absolute MDE is in the metric's own units, with a live-computed
  hint showing what that works out to as a relative MDE against the current
  baseline. Whichever you pick, the wizard back-computes and shows you the
  others.
  `[Screenshot: Parameters step with the MDE mode selector and computed sample size]`
- **Strata** — categorical columns to stratify the split on, so group balance
  holds within each stratum, not just overall.
  - Missing values in a stratifying column: choose to bucket them into their
    own "unknown" stratum (default), drop those users, or treat it as a
    design error and stop.
- **Split method** — `stratified` (recommended when you have strata),
  `simple` (uniform random), or `hash` (deterministic by user ID — same user
  always lands in the same group even across re-splits).
- **Isolation from other active experiments** — how to handle users who are
  also in another currently-running test:
  - `exclude` (recommended) — exclude participants of all active tests.
  - `warn` — show the overlap and ask you to confirm before proceeding.
  - `off` — exclude no one; a deliberate overlap risk.
  - `exclude_selected` — exclude participants of only specific tests you pick.

**Run** produces the split plus a design report: sample size / MDE table per
metric (with and without CUPED, and ρ — the pre/post correlation CUPED
exploits), and the split-quality checks below. Both the design report and the
analysis report (Results tab) offer **View report** (opens in a new browser
tab) and **Download report** (saves it as `<experiment>_design_report.html` /
`<experiment>_report.html`) — either way it's the same self-contained file
(charts, logo, and CSS all inlined), so the downloaded copy opens correctly
offline, with no server needed.

### 3. Sample sizes and split checks

The design report always includes:
- **MDE table** — per metric, the smallest effect size the experiment can
  reliably detect at this sample size, with and without CUPED (works the same
  way for `binary` metrics as `continuous` ones — a conversion-rate metric
  with a pre-period column gets a CUPED-adjusted MDE too, using the
  `p·(1−p)·(1−ρ²)` variance approximation instead of the exact proportions
  test). A dash in a CUPED column always means something specific, not "no
  data": hover it — a metric with no pre-period column shows "no pre-period
  column specified", while a metric that has one but whose correlation with
  it is too weak to matter (|ρ| < 0.1) still shows the computed number, with
  a "low correlation, negligible gain" hint instead of hiding it. Next to
  each relative MDE column is an **MDE (abs.)** column (abs = rel ×
  baseline, shown on hover) — in percentage points for `binary` metrics
  (e.g. a 5% relative MDE on a 17.4% baseline conversion rate is "0.96 pp"),
  or in the metric's own units for `continuous` ones.
- **Stratification** — the Design tab's Configuration panel and the design
  report both state it explicitly: "Stratified by: gender, platform (12
  strata after combination, min stratum size: 20)" when you stratified,
  "Hash-based split (salt stored)" for a `hash` split, or "No
  stratification" for a plain `simple` one. Either way there's a **strata
  balance table** (counts per stratum per group) alongside the pass/fail
  badge — it's the same crosstab the balance chi-square test is computed
  from, just no longer hidden behind a single p-value.
- **SRM check** (Sample Ratio Mismatch) — a chi-square test comparing the
  actual group sizes against the intended split ratio. If it fails
  (p < 0.001), don't trust downstream analysis until you find the cause (a
  splitting bug, filtering applied before export, etc).
- **Data-loss table** — how many assigned users actually show up later in
  post-period data, per group. Loss should be roughly symmetric between
  groups; asymmetric loss can bias the comparison even when SRM passes.
- **Pre-period A/A check** — if you supplied pre-period metric values, ABSet
  runs a quick sanity check that the groups don't already differ before the
  test starts.

### 4. Analyze

Once you have post-period data, open the experiment's **Analyze** tab:

1. Select the dataset with your post-period results (or click **Generate demo
   post-period data (+3% effect)** to try the flow without real data).
2. Open **Advanced options** if you need to change anything — it's collapsed
   by default so most runs don't need to touch it:
   - **Multiple testing correction** — `holm` (default), `bonferroni`,
     `fdr_bh` (Benjamini-Hochberg), or none. This only appears when your
     design actually tests more than one hypothesis (more than one primary
     metric, or more than one treatment group) — with a caption spelling out
     exactly how many (metrics × treatment groups) and why it matters. With
     a single hypothesis, any correction is a no-op, so the control (and the
     **p-value (adj.)** / **Correction** columns in the results table) is
     hidden rather than offered.
   - **Compare alternative methods** — checked by default, so you get
     Welch (raw and with 1% trimming), Welch+CUPED, Bootstrap BCa, and
     Mann-Whitney alongside your designed method without having to think
     about it; useful for sanity-checking that the conclusion is robust
     (these extra rows never factor into the verdict). Uncheck it for faster
     runs on large datasets or weak machines — Bootstrap in particular
     (10k iterations) is the heaviest of the bunch.
3. **Run analysis**. This is an explicit step — preparing/uploading data does
   not run it automatically, so you control exactly when the (final,
   decision-driving) analysis happens.

### 5. Reading results

The **Results** tab has, per metric:

- **Verdict** — `significant positive`, `significant negative`, `no effect
  detected`, or `failed`. Based only on the row where `designed = true` (the
  method declared in your design) and the *adjusted* p-value.
- **Forest plot** — one row per method; the dot is the point estimate, the
  whiskers are the 95% CI of the *relative* lift. The bold/colored row is your
  designed method — that's the one the verdict comes from. If the whiskers
  cross zero, the effect isn't significant. If methods disagree with each
  other, treat the result with extra caution (see the in-app "How do I read
  this chart?" panel on the chart itself for the full breakdown, including what
  disagreement between methods usually means — outliers, skew, or a weak
  covariate for CUPED).
  `[Screenshot: forest plot with the designed method highlighted]`
- **Detailed results table** columns worth knowing:
  - **Effect (abs.)** — absolute difference, test − control, in metric units.
  - **Lift %** — relative effect: (test − control) / control.
  - **95% CI of lift** — confidence interval of the *relative* effect, not of
    the raw metric.
  - **p-value** vs **p-value (adj.)** — the adjusted value is what the
    decision is actually based on; the gap between the two tells you how much
    the multiple-testing correction cost you. With only one primary
    hypothesis (one primary metric, one treatment group), adjustment is a
    no-op, so this column (and **Correction**) is left out of the table
    entirely instead of showing a value identical to the raw p-value.
  - **CUPED ρ** — correlation between the metric and its pre-period
    covariate, shown only on CUPED rows; variance reduction is roughly ρ².
    Low ρ means CUPED isn't helping much for that metric.
  - Rows using **Mann-Whitney** on data with many zero values can legitimately
    show a Hodges-Lehmann shift of exactly 0 — that reflects a skewed
    distribution (e.g. lots of non-converting users), not a bug; the in-app
    tooltip on those rows explains this inline.
- **Distribution / segment / cumulative-lift charts** are diagnostic, not
  decision inputs — each has its own "how to read this" panel in the app.
  Notably: the cumulative-lift chart is for post-hoc storytelling only, never
  for deciding to stop a test early (see "peeking" in the FAQ below), and
  segment breakdowns get no multiple-testing correction — treat a segment-level
  finding as a hypothesis for a follow-up test, not as evidence on its own.

### 6. Publish, conclude, decide

Once you're satisfied with the result, use the **Conclusions and Decision**
text block (next to Hypothesis) to record what you concluded and what you're
doing about it — ship, hold, iterate — then flip the experiment to
`published` so the rest of the org can see it, and to `completed` once data
collection is actually done. Published experiments become part of your
organization's searchable history of what's been tried and what happened,
instead of living in someone's notebook.

### 7. External split mode (Firebase, etc.)

If the random split already happened somewhere else (Firebase A/B Testing and
similar remote-config/experimentation systems), pick **External split** on
the wizard's first step instead of the default **ABSet split**. It changes
the rest of the flow:

- **No dataset step, no split, no assignments, no isolation.** ABSet isn't
  picking or splitting anyone, so none of that applies — the wizard just
  collects the declared design: name, optional hypothesis, group names with
  their *expected* traffic proportions (needed later for the SRM check), and
  metrics. Metric columns are typed in directly (there's no dataset yet to
  pick columns from) rather than chosen from a dropdown.
- **Expected sample size is optional and reference-only.** If you provide
  one, the Design tab shows it as-is; ABSet doesn't compute an MDE table for
  an external split (there's no pre-period data of your candidates to
  compute variance from), and says so explicitly: "external design: power
  calculated by the external system."
- The experiment is created straight into `designed` status with an
  **External split** badge next to the status badges on the experiment page.
  There's no split to redo, so **Redesign** and **Download Samples** aren't
  offered.

**Analyzing an external experiment** adds one mandatory step before you can
run analysis: after selecting your post-period dataset, a **Group
assignment** block appears. Pick the **Group column** — whichever column in
your data holds the variant each row belongs to (e.g. a Firebase experiment
ID/variant column) — and ABSet shows you its distinct values with row counts.
Map each value to one of your declared groups, or to **Exclude** for values
that don't belong to this experiment (bot traffic, an unrelated variant,
etc.); **Run analysis** stays disabled until every declared group has a
mapped value. From there the pipeline is the familiar one:

- **SRM** compares the *actual* proportions in your mapped data against the
  proportions you declared at design time (instead of against an ABSet split
  ratio — same check, different source for "expected").
- The **Multiple testing correction** control appears under the same rule as
  ABSet-split experiments — only when there's more than one hypothesis
  (primary metrics × treatment groups).
- The **data-loss table** (assigned vs. present) doesn't apply — there are no
  assignments to compare against — and is replaced by a **group column
  coverage** note: how many rows had a value that wasn't mapped to any
  declared group and were excluded, and what fraction of the data that is.
- **CUPED** still works exactly the same way, as long as your post-period
  dataset also contains the pre-period column you declared on the metric.
- Verdicts, the results table, forest plots, and the report are unchanged.

## Validation: is the engine honest on your data?

Reach it from **Settings → Tools → Validation (A/A, A/B)** (Editor+; Viewers
don't see the menu item) — it's a service tool for validating a design, not
one of the primary top-nav sections, so it lives in Settings rather than
next to A/B Tests and Datasets. (The old `/validation` URL still works — it
redirects to the new location.)

Validation runs the whole statistical pipeline against *simulated* random
splits of your own historical data — not to test a real hypothesis, but to
test the *test itself*:

- **A/A simulations** verify the false-positive rate (FPR) stays at alpha
  (~5%) when there is no true effect. If your chosen methods are honest, an
  A/A run should call "significant" only about as often as your significance
  threshold allows.
- **A/B simulations** (optional — set an injected effect size) verify the
  engine actually detects an effect of that size at the rate your design's
  power calculation promised (empirical vs. analytical power).

Run it before you trust a design on data you haven't validated before — a
skewed distribution, unexpected clustering, or a bug in a custom metric
definition can quietly break FPR/power guarantees that hold in theory but not
on your specific data. `Number of simulations` needs at least 100 (fewer is
too noisy to interpret); 500 is a quick check, 2000 a strict one.
Results: FPR with a 95% CI and an `honest`/`lying` verdict for A/A; empirical
vs. analytical power (plus the discrepancy between them) for A/B.

## Statistical FAQ

**Why can't I just stop the test early when the cumulative-lift chart looks
good?** This is "peeking" — checking significance repeatedly and stopping the
moment it crosses your threshold. It inflates the real false-positive rate far
above your nominal alpha, because you're effectively running many tests (one
per day you checked) and taking the best-looking one. The decision is based
only on the sample size fixed at design time; the cumulative chart exists for
post-hoc diagnostics only.

**Why the multiple-testing correction?** Every additional primary metric (or
comparison group) you test is another chance for a false positive purely by
chance. Correcting (Holm by default) keeps your overall false-positive rate
at your nominal alpha across all of them, instead of per-comparison — which is
why the adjusted p-value, not the raw one, drives the verdict.

**What does CUPED actually buy me, and how do I pick the pre-period window?**
CUPED reduces the variance of your estimate by subtracting out the part of
the outcome metric that's predictable from a pre-experiment covariate (usually
the same metric, measured before the test) — the more correlated (higher ρ)
the pre-period value is with the outcome, the bigger the variance reduction
(roughly ρ²) and the smaller your required sample size / MDE. Pick a
pre-period window long enough to be a stable, representative measurement of
that user (not so short it's noisy) but recent enough that user behavior
hasn't drifted — for most consumer metrics, a period of similar length to (or
a bit longer than) the test itself is a reasonable starting point; check the
MDE table's ρ column for your actual data before committing to a window, and
try a couple of window lengths if ρ comes out low.

**Why can't I change the sample size / add more users partway through?**
Sample size and MDE were computed together for a fixed alpha/power target
before the test started. Growing the sample mid-flight (or shrinking it by
cutting the test short) without re-planning breaks the guarantees the p-value
and CI are based on — it's a milder form of the same problem as peeking. If
you need a different sample size, that's a new design, not an edit to the
running one.

## Glossary

- **MDE (Minimum Detectable Effect)** — the smallest true effect size an
  experiment can reliably detect given its sample size and power.
- **Power** — probability of detecting a true effect of a given size, if it
  exists.
- **SRM (Sample Ratio Mismatch)** — actual group sizes deviating from the
  intended split ratio by more than chance would explain; a red flag for the
  whole experiment's data integrity.
- **CUPED** — variance-reduction technique using a pre-experiment covariate
  correlated with the outcome metric.
- **ρ (rho)** — correlation between a metric and its pre-period covariate;
  drives how much CUPED helps.
- **Isolation** — excluding (or flagging) users who are simultaneously
  participating in another active experiment, to avoid interaction effects
  between tests.
- **Verdict** — the app's automated read of a primary metric's designed-method
  result: significant positive/negative, no effect detected, or failed.
- **Designed method** — the specific statistical method declared during
  Design as the one the decision will be based on; other methods computed via
  "Compare alternative methods" are for robustness-checking only.
- **A/A test** — a simulated or real comparison between two groups that
  should have no difference, used to check the methodology (and, for a real
  A/A on live traffic, the instrumentation) is honest.
- **Peeking** — checking test results before the planned sample size is
  reached and using that to decide whether to stop; inflates false positives.
