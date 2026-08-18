import { Typography, Input, Select, Upload, Image, Button, message } from 'antd'
import { DeleteOutlined, InboxOutlined } from '@ant-design/icons'
import type { UploadProps } from 'antd'
import {
  DndContext, closestCenter, PointerSensor, useSensor, useSensors,
} from '@dnd-kit/core'
import type { DragEndEvent } from '@dnd-kit/core'
import { SortableContext, useSortable, arrayMove, rectSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { nextId } from './types'
import type { WizardState, FlowColumnState, FlowImageState } from './types'
import { mb, mt } from './spacing'

// Stage 4 (CLAUDE.md, variant flow images) limits — mirrors
// abkit/flow_images.py's MAX_FILE_BYTES/MAX_IMAGES_PER_GROUP; enforced here
// too for immediate feedback before a round trip to the server (the server
// re-checks both regardless, this is UX only, not the real guard).
const MAX_IMAGES_PER_GROUP = 10
const MAX_FILE_BYTES = 5 * 1024 * 1024
const ACCEPTED_TYPES = ['image/png', 'image/jpeg', 'image/webp']

interface Props {
  state: WizardState
  setState: (updater: (prev: WizardState) => WizardState) => void
}

// Columns are derived 1:1 from state.groups (not independently added/
// removed) — "Экран разбит на колонки ПО ЧИСЛУ групп" — but each column's
// groupName binding is its own editable field (defaults to groups[i], can
// be repointed), and its images/title persist across group-count changes
// via state.flowColumns, keyed by array position. This backfills/truncates
// state.flowColumns to exactly state.groups.length without ever discarding
// an already-populated column's content just because the array was shorter
// than expected (e.g. right after a fresh load).
export function getColumns(state: WizardState): FlowColumnState[] {
  return state.groups.map((g, i) => {
    const existing = state.flowColumns[i]
    if (existing) return existing
    return { id: nextId('flowcol'), groupName: g.name, flowTitle: '', images: [] }
  })
}

function SortableThumb({ image, onDelete }: { image: FlowImageState; onDelete: () => void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: image.id })
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  }
  return (
    <div
      ref={setNodeRef}
      data-testid={`flow-thumb-${image.id}`}
      style={{ ...style, position: 'relative', width: 84, height: 84, touchAction: 'none' }}
      {...attributes}
      {...listeners}
    >
      <Image
        src={image.previewUrl}
        width={84}
        height={84}
        style={{ objectFit: 'cover', borderRadius: 4, cursor: 'grab' }}
        preview={{ mask: false }}
      />
      <Button
        size="small"
        danger
        icon={<DeleteOutlined />}
        onClick={(e) => {
          e.stopPropagation()
          onDelete()
        }}
        style={{ position: 'absolute', top: -8, right: -8, minWidth: 22, width: 22, height: 22, padding: 0 }}
      />
    </div>
  )
}

function FlowColumn({
  column, onChange,
}: {
  column: FlowColumnState
  onChange: (patch: Partial<FlowColumnState>) => void
}) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }))

  const addFiles = (files: File[]) => {
    const room = MAX_IMAGES_PER_GROUP - column.images.length
    if (room <= 0) {
      message.error(`Group '${column.groupName}' already has ${MAX_IMAGES_PER_GROUP} images`)
      return
    }
    const accepted: FlowImageState[] = []
    for (const file of files.slice(0, room)) {
      if (!ACCEPTED_TYPES.includes(file.type)) {
        message.error(`${file.name}: only PNG/JPEG/WEBP images are supported`)
        continue
      }
      if (file.size > MAX_FILE_BYTES) {
        message.error(`${file.name} exceeds the 5 MB limit`)
        continue
      }
      accepted.push({ id: nextId('flowimg'), kind: 'new', file, previewUrl: URL.createObjectURL(file) })
    }
    if (accepted.length) onChange({ images: [...column.images, ...accepted] })
  }

  const uploadProps: UploadProps = {
    accept: ACCEPTED_TYPES.join(','),
    multiple: true,
    showUploadList: false,
    beforeUpload: (_file, fileList) => {
      addFiles(fileList)
      return false // never let AntD auto-upload — files are staged until Step4Review submits
    },
  }

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = column.images.findIndex((i) => i.id === active.id)
    const newIndex = column.images.findIndex((i) => i.id === over.id)
    if (oldIndex === -1 || newIndex === -1) return
    onChange({ images: arrayMove(column.images, oldIndex, newIndex) })
  }

  return (
    <div style={{ border: '1px solid #E8E8E8', borderRadius: 6, padding: 12 }}>
      <Input
        placeholder="Flow title, e.g. Checkout — new design (optional)"
        value={column.flowTitle}
        onChange={(e) => onChange({ flowTitle: e.target.value })}
        style={{ ...mb('FIELD') }}
      />
      <Upload.Dragger {...uploadProps} style={{ padding: '8px 0', ...mb('BLOCK') }}>
        <p className="ant-upload-drag-icon" style={{ margin: '4px 0' }}>
          <InboxOutlined />
        </p>
        <p style={{ fontSize: 12, margin: 0 }}>Drag images here, or click to choose</p>
        <p style={{ fontSize: 11, color: '#999', margin: 0 }}>
          PNG/JPEG/WEBP, up to 5MB each, {column.images.length}/{MAX_IMAGES_PER_GROUP}
        </p>
      </Upload.Dragger>
      {column.images.length > 0 && (
        <Image.PreviewGroup>
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={column.images.map((i) => i.id)} strategy={rectSortingStrategy}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                {column.images.map((image) => (
                  <SortableThumb
                    key={image.id}
                    image={image}
                    onDelete={() => onChange({ images: column.images.filter((i) => i.id !== image.id) })}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        </Image.PreviewGroup>
      )}
    </div>
  )
}

export function FlowImagesSection({ state, setState }: Props) {
  const columns = getColumns(state)

  const updateColumn = (index: number, patch: Partial<FlowColumnState>) => {
    setState((prev) => {
      const cols = getColumns(prev)
      cols[index] = { ...cols[index], ...patch }
      return { ...prev, flowColumns: cols }
    })
  }

  if (state.groups.filter((g) => g.name.trim()).length === 0) return null

  // >3 groups: horizontal scroll of equal-width cards; 2-3: even columns
  // that fill the row (halves/thirds) — CLAUDE.md Stage 4 item 4.1.
  const manyColumns = columns.length > 3
  const usedGroupNames = new Set(columns.map((c) => c.groupName))

  return (
    <div style={{ ...mt('SECTION'), ...mb('SECTION') }}>
      <Typography.Title level={5}>Variant flows (optional)</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8, fontSize: 13 }}>
        Optional screenshots showing what each variant looks like — shown on the Design tab and in the design
        report. Editable later only via Redesign.
      </Typography.Paragraph>
      <div
        style={{
          display: 'flex',
          gap: 16,
          overflowX: manyColumns ? 'auto' : undefined,
          paddingBottom: manyColumns ? 8 : undefined,
        }}
      >
        {columns.map((column, i) => (
          <div
            key={column.id}
            data-testid={`flow-column-${i}`}
            style={manyColumns ? { minWidth: 280, flexShrink: 0 } : { flex: 1, minWidth: 0 }}
          >
            <Select
              value={column.groupName}
              style={{ width: '100%', ...mb('FIELD') }}
              onChange={(groupName) => updateColumn(i, { groupName })}
              options={state.groups
                .filter((g) => g.name.trim())
                .map((g) => ({
                  value: g.name,
                  label: g.name,
                  // A group already bound to ANOTHER column is disabled here
                  // — two columns sharing one group_name would collide at
                  // submit time (Step4Review's per-group order call would
                  // overwrite one column's images with the other's).
                  disabled: g.name !== column.groupName && usedGroupNames.has(g.name),
                }))}
            />
            <FlowColumn column={column} onChange={(patch) => updateColumn(i, patch)} />
          </div>
        ))}
      </div>
    </div>
  )
}
