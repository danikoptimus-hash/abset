// "How to read this chart/table" texts — kept in sync with
// abkit/viz/help_texts.py (same text is used in the HTML report; TS can't
// import Python, so this is a duplicate copy — check both when editing).

export const HELP_TEXTS: Record<string, string> = {
  forest: `**What is shown**

The horizontal axis is the effect estimate (relative lift, in percent). Each row is a separate statistical test or combination of tests (Welch t-test, Welch+CUPED, Bootstrap BCa, etc). The dot is the point estimate of the effect, the horizontal line is the 95% confidence interval (CI — the range of effect values consistent with the data). The bold (colored) row is the method declared in the experiment design: this is the one the decision is based on. The dashed vertical line at zero is the "no effect" boundary.

**How to read it**

If the whole CI (whiskers) is to the right of zero, the effect is significantly positive. If the whole CI is to the left of zero, it's significantly negative. If the CI crosses zero, the effect is not significant: it can't be distinguished from random fluctuation. Narrower whiskers mean a more precise estimate (usually narrower for variance-reduction methods like CUPED). If all methods agree (all to the right or all to the left of zero), the conclusions are robust. If the methods disagree, the effect is sensitive to the choice of test and should be treated with caution.

**When something looks off**

Different methods give fundamentally different results (e.g. Welch shows an effect but Bootstrap doesn't). This often means outliers or a strongly skewed distribution. The CUPED method's CI isn't narrower than without CUPED — the covariate is weakly correlated with the metric, so CUPED didn't help. Mann-Whitney and the t-test disagree substantially — the distribution is heavily skewed.`,

  distribution_continuous: `**What is shown**

Overlaid, semi-transparent histograms for control (one color) and treatment (another) — a visualization of how the metric's values are distributed in each group. Plus the ECDF (empirical cumulative distribution function — a cumulative curve): for each value X it shows the share of users with a metric value ≤ X. The vertical dashed line is the outlier-clipping threshold, if one was applied.

**How to read it**

The histograms show the shape of the distribution. If the control and treatment histograms nearly overlap, there's likely no effect. If treatment is shifted right relative to control, there's a positive effect. The ECDF curves make the shift easier to see for skewed distributions: if the treatment curve is shifted right of control, then at the same metric value a smaller share of treatment users has reached that bar — meaning treatment has more high values.

**When something looks off**

A long thin tail on the right — outliers may be distorting the means; consider clipping or using bootstrap/Mann-Whitney. A pile of observations at zero plus a distribution to the right — a typical "bought/didn't buy" pattern (lots of zeros plus a distribution among buyers); for such a metric it can make sense to compute the effect only on converted users, or use a ratio approach.`,

  distribution_binary: `**What is shown**

Two side-by-side bars — the conversion rate (or other positive-event rate) in control and treatment. The whiskers above the bars are the 95% confidence interval (CI) for the proportion, computed via the Wilson score interval. The labels above the bars are the exact proportion and half the CI width.

**How to read it**

The difference in bar heights is the estimated absolute effect in percentage points. If the bars' whiskers don't overlap (the CIs share no values), the effect is definitely significant. If the whiskers overlap slightly, the effect may be borderline — check the forest plot for the precise verdict.

**When something looks off**

One or both CIs cross zero at a very low conversion rate — there isn't enough data for a confident estimate. The difference between groups is very large (several-fold) — worth checking data quality, there may be a problem with group assignment.`,

  cumulative_lift: `**What is shown**

The horizontal axis is days since the start of the test. The vertical axis is relative lift, in percent. The line is the effect estimate computed on data accumulated from day 1 through the given day of the test. The shaded band around the line is the 95% confidence interval (CI). The horizontal dashed line is the "no effect" boundary (lift = 0%).

**How to read it**

The chart shows how the effect estimate changed as data accumulated. Early in the test the CI is very wide (little data); it narrows toward the end. If the line stabilizes at a certain level, the sample is sufficient and the estimate is reliable. If the line is still fluctuating heavily near the end of the test, there wasn't enough data, and the apparent effect may not be real.

**When something looks off**

A sharp spike in the first few days followed by a drop to a lower value — looks like a novelty effect (users react strongly to the novelty, but the effect isn't durable). Sharp spikes on individual days — likely marketing activity, a technical incident, or another external event. The trend hasn't stabilized by the end of the test — the test was too short.`,

  segment_forest: `**What is shown**

The same forest plot, but each row is the metric's effect within a single segment (stratum), e.g. iOS/Android or country. The overall effect across the whole sample is shown separately, for comparison.

**How to read it**

Look at how uniform the effect is: if the confidence intervals (CIs) of all segments overlap the overall estimate and each other, the effect is roughly the same across segments. If segments diverge sharply (one positive, another negative), the effect is heterogeneous, and the overall estimate is masking different behavior across groups.

**When something looks off**

Segment breakdowns are exploratory (they're hypotheses, not a validated result). No multiple-testing correction is applied to them. Don't treat segment-level decisions as primary — these are hypotheses that need to be validated with a separate test. Using segment results as primary evidence inflates the false-positive rate.`,

  verdicts_table: `**What is shown**

One row per metric × method × treatment-group pair: the point estimate of the effect (absolute and relative), the p-value (raw and after multiple-testing correction), whether the method was declared in the design (designed), and the metric's role (primary/secondary).

**How to read it**

The decision is based only on rows with designed=True and p-adj (the corrected p-value). If p-adj < alpha (usually 0.05) and the effect is positive, the verdict is "significant positive"; if negative, "significant negative"; otherwise, no effect was detected. Rows with designed=False are alternative methods used to check the robustness of the conclusion — they don't factor into the verdict.

**When something looks off**

p-value and p-adj diverge substantially — there are many other comparisons around this metric, and the multiple-testing correction is meaningfully reducing significance. Secondary metrics are marked as exploratory: treat their verdicts as hypotheses, not as grounds for a decision.`,

  srm_table: `**What is shown**

The SRM (Sample Ratio Mismatch) table: how many users were actually observed in each group versus how many were expected from the configured split proportions, plus a chi-square test of their agreement. Below it, the data-loss table: how many users were assigned to a group, how many actually showed up in the post-period data, and the loss rate for each group.

**How to read it**

If the SRM test's p-value is ≥ 0.001, the actual split matches the intended one — everything's fine. Data loss should be roughly the same proportion in both groups — then it doesn't bias the comparison.

**When something looks off**

SRM fails (p-value < 0.001) — the actual group proportions differ statistically significantly from the intended ones; the analysis results shouldn't be trusted in this case until the cause is found (a bug in the split, filtering applied before the export, etc). Loss is asymmetric between groups (e.g. one group has noticeably more loss) — something about the drop-off rate correlates with the group, and the final comparison may be biased even if SRM passed.`,

  mde_table: `**What is shown**

For each metric: the minimum detectable effect (MDE), in relative terms, for the given sample size and power; the same MDE accounting for CUPED (if a pre-period column is available); the group size; and ρ (the correlation between the pre-period and current metric value — how much CUPED can reduce variance).

**How to read it**

MDE is the smallest effect the experiment can reliably detect statistically at the given sample size. If the expected real effect is smaller than the MDE, the test likely won't show a significant result even if the effect actually exists. The higher ρ is, the more CUPED reduces the MDE.

**When something looks off**

MDE is much larger than the expected real effect — the sample isn't big enough; either increase the experiment's size/duration, or reduce variance (stratification, CUPED, outlier clipping). MDE with CUPED is barely different from MDE without it — ρ is low, the covariate doesn't help much, don't count on a gain from CUPED.`,
}

export const CHART_WARNINGS: Record<string, string> = {
  cumulative_lift:
    "This chart is for post-hoc diagnostics, not for stopping the test. The decision is based only on the last day fixed in the design. Stopping the test the moment an intermediate day looks good (peeking) breaks the statistics and inflates the false-positive rate.",
  segment_forest:
    "Segment breakdowns are exploratory. No multiple-testing correction is applied to them. Don't treat segment-level decisions as primary — these are hypotheses that need to be validated with a separate test. Using segment results as primary evidence inflates the false-positive rate.",
}
