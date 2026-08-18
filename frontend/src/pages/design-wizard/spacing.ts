// Item A4 — one vertical-rhythm scale for the design wizard and the Analyze
// tab, replacing the per-block magic numbers those screens had accumulated
// (marginBottom of 4/8/12/16/24 chosen independently per block, so two
// adjacent inputs could sit 8px apart on one step and 12px apart on the next).
//
// Four steps, each with one job — pick by ROLE, not by how big it looks:
//
//   HINT    a hint/caption sitting under the control it explains. Deliberately
//           the tightest gap: the caption must read as part of the control
//           above it, not as a separate thing.
//   FIELD   between two controls inside the same logical block (two inputs of
//           one metric row, a select and the select under it).
//   BLOCK   between logical blocks inside one section (one metric card and the
//           next; a group row and the next group row).
//   SECTION between titled sections of a step (Groups -> Metrics), and before
//           a step's primary action.
//
// Exported as plain numbers rather than a CSS/AntD theme token because every
// consumer here is an inline `style={{ marginBottom: ... }}` on an AntD
// component; a token would need a provider and would still be spelled out at
// each call site. The point of this module is that the VALUES live in one
// place and are chosen by name, not that the mechanism changes.
export const SPACE = {
  HINT: 4,
  FIELD: 8,
  BLOCK: 16,
  SECTION: 24,
} as const

/** `marginBottom` at a given step — `<div style={mb('BLOCK')}>`. */
export function mb(step: keyof typeof SPACE): { marginBottom: number } {
  return { marginBottom: SPACE[step] }
}

/** `marginTop` at a given step, for the rarer "push away from what's above". */
export function mt(step: keyof typeof SPACE): { marginTop: number } {
  return { marginTop: SPACE[step] }
}
