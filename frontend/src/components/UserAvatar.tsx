import { Avatar, Tooltip } from 'antd'

// Muted palette (no orange, matches the theme) — deterministic pick by
// hashing the user id so the same person always gets the same color.
const PALETTE = ['#5B8C7E', '#6B7FA3', '#8C6B9E', '#A37F5B', '#5B7FA3', '#7E8C5B']

function hashColor(id: string): string {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0
  }
  return PALETTE[hash % PALETTE.length]
}

function initialsOf(firstName: string, lastName: string, email: string): string {
  const combined = `${firstName?.[0] ?? ''}${lastName?.[0] ?? ''}`.toUpperCase()
  return combined || email.slice(0, 2).toUpperCase()
}

export interface AvatarUser {
  id: string
  firstName: string
  lastName: string
  email: string
}

// Single avatar with initials + hash-colored background + full name/email
// tooltip (UX package, section 2) — Owner column in the experiments list.
export function UserAvatar({ user }: { user: AvatarUser }) {
  const fullName = `${user.firstName} ${user.lastName}`.trim() || user.email
  return (
    <Tooltip title={`${fullName} · ${user.email}`}>
      <Avatar size="small" style={{ backgroundColor: hashColor(user.id) }}>
        {initialsOf(user.firstName, user.lastName, user.email)}
      </Avatar>
    </Tooltip>
  )
}

// Overlapping avatars for multiple owners/editors (UX package, section 2.3)
// — a group of one renders identically to a single UserAvatar, so this is
// safe to use everywhere even before multi-owner data is wired in.
export function UserAvatarGroup({ users, maxCount = 3 }: { users: AvatarUser[]; maxCount?: number }) {
  if (users.length === 0) return null
  return (
    <Avatar.Group max={{ count: maxCount }}>
      {users.map((u) => (
        <UserAvatar key={u.id} user={u} />
      ))}
    </Avatar.Group>
  )
}
