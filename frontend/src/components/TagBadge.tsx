import { Tag, Tooltip } from 'antd'
import { hashColor } from './hashColor'

export interface TagLike {
  id: string
  name: string
}

// Single tag badge — deterministic muted color by hash of the NAME (not id,
// unlike UserAvatar — two tags named "Growth" should always look the same
// even if something ever produced two rows, and it means the color a user
// sees while typing a not-yet-created name in the Properties Select already
// matches what it'll look like once saved). Optional onClick makes it act as
// a list filter (UX package, Tags §3.5) — clicking a badge anywhere filters
// the experiments list by that tag.
export function TagBadge({ tag, onClick }: { tag: TagLike; onClick?: (tag: TagLike) => void }) {
  return (
    <Tag
      color={hashColor(tag.name)}
      style={onClick ? { cursor: 'pointer' } : undefined}
      onClick={
        onClick
          ? (e) => {
              e.stopPropagation()
              onClick(tag)
            }
          : undefined
      }
    >
      {tag.name}
    </Tag>
  )
}

// Compact row of badges for the experiments list's Tags column — more than
// maxVisible collapses into a single "+N" badge with a tooltip listing the
// rest (UX package, Tags §3.4), so one experiment with many tags doesn't
// blow out the row height.
export function TagList({
  tags, maxVisible = 2, onTagClick,
}: {
  tags: TagLike[]
  maxVisible?: number
  onTagClick?: (tag: TagLike) => void
}) {
  if (tags.length === 0) return null
  const visible = tags.slice(0, maxVisible)
  const overflow = tags.slice(maxVisible)
  return (
    <>
      {visible.map((t) => (
        <TagBadge key={t.id} tag={t} onClick={onTagClick} />
      ))}
      {overflow.length > 0 && (
        <Tooltip title={overflow.map((t) => t.name).join(', ')}>
          <Tag>+{overflow.length}</Tag>
        </Tooltip>
      )}
    </>
  )
}
