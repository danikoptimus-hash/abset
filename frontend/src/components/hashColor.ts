// Muted palette (no orange, matches the theme) — deterministic pick by
// hashing a string (user id, tag name, ...) so the same input always gets
// the same color. Shared by UserAvatar (owner avatars) and tag badges
// (components/TagBadge.tsx) — one palette, one hash, not two copies.
const PALETTE = ['#5B8C7E', '#6B7FA3', '#8C6B9E', '#A37F5B', '#5B7FA3', '#7E8C5B']

export function hashColor(seed: string): string {
  let hash = 0
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0
  }
  return PALETTE[hash % PALETTE.length]
}
