// Тексты перенесены из app.py (_render_design_intro и соседние константы) —
// FRONTEND.md §5.2 шаг 1: "экспандеры-подсказки (перенести существующие тексты)".

export const DESIGN_EXAMPLE_ROWS = [
  { user_id: 'u_00001', platform: 'ios', country: 'RU', segment: 'premium', converted_pre_30d: 1, revenue_pre_30d: 1240, sessions_pre_30d: 12 },
  { user_id: 'u_00002', platform: 'android', country: 'UZ', segment: 'free', converted_pre_30d: 0, revenue_pre_30d: 0, sessions_pre_30d: 3 },
  { user_id: 'u_00003', platform: 'ios', country: 'KZ', segment: 'premium', converted_pre_30d: 1, revenue_pre_30d: 890, sessions_pre_30d: 8 },
  { user_id: 'u_00004', platform: 'android', country: 'RU', segment: 'free', converted_pre_30d: 0, revenue_pre_30d: 0, sessions_pre_30d: 1 },
  { user_id: 'u_00005', platform: 'web', country: 'UZ', segment: 'premium', converted_pre_30d: 1, revenue_pre_30d: 2100, sessions_pre_30d: 15 },
  { user_id: 'u_00006', platform: 'ios', country: 'RU', segment: 'free', converted_pre_30d: 0, revenue_pre_30d: 340, sessions_pre_30d: 5 },
]

export const DESIGN_SQL_EXAMPLE = `SELECT
    user_id,
    any(platform) as platform,
    any(country) as country,
    any(segment) as segment,
    -- binary pre-period metrics
    max(if(event = 'purchase', 1, 0)) as converted_pre_30d,
    -- continuous pre-period metrics
    sum(if(event = 'purchase', revenue, 0)) as revenue_pre_30d,
    count(distinct session_id) as sessions_pre_30d
FROM events
WHERE date >= today() - 30 AND date < today()
GROUP BY user_id`

export const WHAT_IS_THIS_DATA = `This is a snapshot of your user base **BEFORE** the test — the people you might include in the experiment.

**Format:** one row = one user.

**What the file should contain:**
- A user ID column (required, unique)
- Attributes for stratification: platform, country, segment, plan, etc. (recommended — without them groups won't be balanced)
- Pre-period metrics: the same metrics you'll measure in the test, but for the period BEFORE the test (recommended — CUPED and an accurate MDE calculation don't work without them)`

export const EXAMPLE_EXPLANATION = `- **user_id** — unique identifier (required)
- **platform, country, segment** — attributes for stratification (any categorical columns work, more is better for balance)
- **converted_pre_30d** — binary pre-period metric (0/1) for the future conversion analysis
- **revenue_pre_30d** — continuous pre-period metric for revenue
- **sessions_pre_30d** — number of sessions, needed for ratio metrics like revenue/sessions`

export const SQL_EXPLANATION = `Replace \`event = 'purchase'\` with your conversion event. Choose the period (30 days) so it makes sense for your product — a typical decision window.`

export const NO_DATA_EXPLANATION = `Click **"Demo Data"**. The app will generate a synthetic dataset of 5000 users with a realistic structure (different platforms, countries, segments, pre-period metrics) and walk you through the entire workflow — from design to the analysis report. This is the best way to see how the tool works.`

export const SPLIT_METHOD_LABELS: Record<string, string> = {
  stratified: 'stratified — split within each stratum separately (best group balance)',
  simple: 'simple — random split ignoring strata (largest remainder)',
  hash: 'hash — deterministic split by sha256(salt + unit_id), does not guarantee stratum balance',
}

export const ISOLATION_LABELS: Record<string, string> = {
  exclude: 'exclude — exclude participants of all active tests (recommended)',
  warn: 'warn — show the overlap and ask for confirmation',
  off: 'off — exclude no one (a deliberate overlap risk)',
  exclude_selected: 'exclude_selected — exclude participants of only the selected tests',
}

export const NAN_STRATEGY_LABELS: Record<string, string> = {
  separate_stratum: "Put into a separate 'unknown' stratum (default)",
  drop: 'Remove users with missing values',
  error: 'Treat as a design error',
}

export const SIZE_MODE_LABELS: Record<string, string> = {
  mde_rel: 'Set a target relative MDE',
  mde_abs: 'Set a target absolute MDE',
  sample_size: 'Set a sample size',
  all: 'Use all available data',
}

export const GROUP_PRESETS: Record<string, Record<string, number>> = {
  '50/50': { control: 0.5, treatment: 0.5 },
  '90/10': { control: 0.9, treatment: 0.1 },
  '33/33/33': { control: 0.34, treatment_a: 0.33, treatment_b: 0.33 },
}
