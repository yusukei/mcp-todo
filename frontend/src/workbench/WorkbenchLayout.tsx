import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { Group, Panel, Separator, type Layout } from 'react-resizable-panels'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  pointerWithin,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragMoveEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import type { LayoutTree, PaneType } from './types'
import { MAX_TAB_GROUPS, MAX_TABS_PER_GROUP } from './types'
import TabGroup from './TabGroup'
import { countTabGroups, findTabsNode } from './treeUtils'
import type { DropEdge } from './treeUtils'
import { showInfoToast } from '../components/common/Toast'
import { classifyTabInsertIndex, classifyZone } from './dndZones'
import type { DropZone } from './dndZones'
import {
  DragStateContext,
  parseDragId,
  parseGroupDropId,
  type DragState,
} from './dndContext'
import { PANE_TYPE_LABELS } from './paneRegistry'

interface Props {
  tree: LayoutTree
  projectId: string

  onActivateTab: (groupId: string, tabId: string) => void
  onCloseTab: (groupId: string, tabId: string) => void
  onAddTab: (groupId: string, paneType: PaneType) => void
  onConfigChange: (paneId: string, patch: Record<string, unknown>) => void
  onSplit: (groupId: string, orientation: 'horizontal' | 'vertical') => void
  onCloseGroup: (groupId: string) => void
  onSplitSizes: (splitId: string, sizes: number[]) => void
  onMoveTab: (
    paneId: string,
    targetGroupId: string,
    drop: { kind: 'edge'; edge: DropEdge } | { kind: 'center'; index: number },
  ) => void
  // Phase 3: page-level actions previously surfaced by the WorkbenchPage
  // header. They now live on every TabGroup's ⋮ menu / right-rail icon.
  // ``projectName`` lets TabGroup show a breadcrumb when the SidebarRail
  // hides the project label.
  projectName: string
  onLoadPreset: (presetId: string) => void
  onResetLayout: () => void
  onCopyUrl: () => void
}

/**
 * Top-level layout renderer.
 *
 * Mounts a single ``<DndContext>`` so tab-header drag interactions
 * can target every tab group on the page. Drag-time hit testing for
 * the 5-zone overlay is computed *outside* dnd-kit's collision
 * algorithm: we register one droppable per group and read the live
 * pointer position from drag events to refine into one of
 * top/right/bottom/left/center plus an insertion index for tabify
 * drops. dnd-kit's own collision detection only narrows down which
 * group the pointer is over.
 */
export default function WorkbenchLayout(props: Props) {
  const total = countTabGroups(props.tree)
  return <DnDWrapper {...props} totalGroups={total} />
}

interface DnDWrapperProps extends Props {
  totalGroups: number
}

function DnDWrapper({
  tree,
  projectId,
  projectName,
  totalGroups,
  onActivateTab,
  onCloseTab,
  onAddTab,
  onConfigChange,
  onSplit,
  onCloseGroup,
  onSplitSizes,
  onMoveTab,
  onLoadPreset,
  onResetLayout,
  onCopyUrl,
}: DnDWrapperProps) {
  const sensors = useSensors(
    // 5px activation distance — clicks on tab headers (which are also
    // draggable) still work because dnd-kit only takes over once the
    // pointer travels.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  // Latest drag state. Mirrored to React state so descendants
  // re-render with overlay highlights.
  const [dragState, setDragState] = useState<DragState>({
    active: null,
    hover: null,
  })

  // The dragged pane snapshot for the DragOverlay preview. Captured
  // at drag start so a fast-moving cross-group drop still has the
  // correct label when the source pane is removed mid-flight.
  const dragLabelRef = useRef<string | null>(null)

  // Reduce-motion preference, sampled once + on change.
  const [reducedMotion, setReducedMotion] = useState<boolean>(() => {
    try {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches
    } catch {
      return false
    }
  })
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const handler = (e: MediaQueryListEvent) => setReducedMotion(e.matches)
    mq.addEventListener?.('change', handler)
    return () => mq.removeEventListener?.('change', handler)
  }, [])

  // ── dnd-kit handlers ────────────────────────────────────────

  const findSourceGroupId = useCallback(
    (paneId: string): string | null => {
      function walk(t: LayoutTree): string | null {
        if (t.kind === 'tabs') {
          return t.tabs.some((p) => p.id === paneId) ? t.id : null
        }
        for (const c of t.children) {
          const r = walk(c)
          if (r) return r
        }
        return null
      }
      return walk(tree)
    },
    [tree],
  )

  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const paneId = parseDragId(event.active.id)
      if (!paneId) return
      const sourceGroupId = findSourceGroupId(paneId)
      if (!sourceGroupId) return
      // Snapshot the dragged pane's label for the DragOverlay.
      const data = event.active.data.current as
        | { paneType?: PaneType }
        | undefined
      dragLabelRef.current = data?.paneType
        ? PANE_TYPE_LABELS[data.paneType]
        : 'Tab'
      setDragState({
        active: { paneId, sourceGroupId },
        hover: null,
      })
    },
    [findSourceGroupId],
  )

  const handleDragMove = useCallback((event: DragMoveEvent) => {
    setDragState((prev) => {
      if (!prev.active) return prev
      const overGroupId = parseGroupDropId(event.over?.id)
      if (!overGroupId || !event.over) {
        if (prev.hover === null) return prev
        return { ...prev, hover: null }
      }
      // Compute current pointer position from the activator event +
      // the cumulative drag delta. PointerEvent / MouseEvent both
      // expose clientX/Y, but @types/dnd-kit types ``activatorEvent``
      // as ``Event`` so we narrow defensively.
      const activator = event.activatorEvent as
        | (Event & { clientX?: number; clientY?: number })
        | null
      if (
        !activator ||
        typeof activator.clientX !== 'number' ||
        typeof activator.clientY !== 'number'
      ) {
        return prev
      }
      const px = activator.clientX + event.delta.x
      const py = activator.clientY + event.delta.y
      const rect = event.over.rect
      const zone = classifyZone(
        {
          left: rect.left,
          top: rect.top,
          right: rect.left + rect.width,
          bottom: rect.top + rect.height,
        },
        { x: px, y: py },
      )
      if (!zone) {
        if (prev.hover === null) return prev
        return { ...prev, hover: null }
      }
      let insertIndex = -1
      if (zone === 'center') {
        insertIndex = computeInsertIndex(overGroupId, px)
      }
      if (
        prev.hover &&
        prev.hover.groupId === overGroupId &&
        prev.hover.zone === zone &&
        prev.hover.insertIndex === insertIndex
      ) {
        return prev
      }
      return {
        ...prev,
        hover: { groupId: overGroupId, zone, insertIndex },
      }
    })
  }, [])

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const paneId = parseDragId(event.active.id)
      const overGroupId = parseGroupDropId(event.over?.id)
      const hover = dragState.hover
      // Reset state regardless of outcome so we don't leak overlay.
      setDragState({ active: null, hover: null })
      dragLabelRef.current = null

      if (!paneId || !overGroupId || !hover || hover.groupId !== overGroupId) {
        // Drop outside a group / before any over event landed → no-op.
        return
      }

      // Cap checks: tell the user *why* a drop was rejected rather
      // than silently no-op'ing.
      if (hover.zone !== 'center') {
        if (totalGroups >= MAX_TAB_GROUPS) {
          showInfoToast(`Maximum ${MAX_TAB_GROUPS} panes allowed.`)
          return
        }
      } else {
        const target = findTabsNode(tree, overGroupId)
        const sameGroup = dragState.active?.sourceGroupId === overGroupId
        if (
          target &&
          !sameGroup &&
          target.tabs.length >= MAX_TABS_PER_GROUP
        ) {
          showInfoToast(
            `Tab group is full (${MAX_TABS_PER_GROUP} tabs).`,
          )
          return
        }
      }

      if (hover.zone === 'center') {
        // Same-group center drop on the same pane index = no-op.
        const sourceLoc = dragState.active?.sourceGroupId === overGroupId
        if (sourceLoc) {
          // Reorder; index already adjusted in the tree util.
        }
        onMoveTab(paneId, overGroupId, {
          kind: 'center',
          index: Math.max(0, hover.insertIndex),
        })
        return
      }
      onMoveTab(paneId, overGroupId, { kind: 'edge', edge: hover.zone })
    },
    [dragState, onMoveTab, tree, totalGroups],
  )

  const handleDragCancel = useCallback(() => {
    setDragState({ active: null, hover: null })
    dragLabelRef.current = null
  }, [])

  // ── Render ──────────────────────────────────────────────────

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={pointerWithin}
      onDragStart={handleDragStart}
      onDragMove={handleDragMove}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      <DragStateContext.Provider value={dragState}>
        <Renderer
          tree={tree}
          projectId={projectId}
          projectName={projectName}
          totalGroups={totalGroups}
          reducedMotion={reducedMotion}
          isPrimary={true}
          onActivateTab={onActivateTab}
          onCloseTab={onCloseTab}
          onAddTab={onAddTab}
          onConfigChange={onConfigChange}
          onSplit={onSplit}
          onCloseGroup={onCloseGroup}
          onSplitSizes={onSplitSizes}
          onLoadPreset={onLoadPreset}
          onResetLayout={onResetLayout}
          onCopyUrl={onCopyUrl}
        />
      </DragStateContext.Provider>
      <DragOverlay dropAnimation={reducedMotion ? null : undefined}>
        {dragState.active ? (
          <div className="px-3 py-1 text-xs rounded bg-accent-500 text-white shadow-lg pointer-events-none">
            {dragLabelRef.current ?? 'Tab'}
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  )
}

interface RendererProps {
  tree: LayoutTree
  projectId: string
  projectName: string
  totalGroups: number
  reducedMotion: boolean
  /** True only for the leftmost / topmost TabGroup so the page-level
   *  actions (Layout / Reset / Copy URL / breadcrumb) appear once. */
  isPrimary: boolean
  onActivateTab: (groupId: string, tabId: string) => void
  onCloseTab: (groupId: string, tabId: string) => void
  onAddTab: (groupId: string, paneType: PaneType) => void
  onConfigChange: (paneId: string, patch: Record<string, unknown>) => void
  onSplit: (groupId: string, orientation: 'horizontal' | 'vertical') => void
  onCloseGroup: (groupId: string) => void
  onSplitSizes: (splitId: string, sizes: number[]) => void
  onLoadPreset: (presetId: string) => void
  onResetLayout: () => void
  onCopyUrl: () => void
}

function Renderer({
  tree,
  projectId,
  projectName,
  totalGroups,
  reducedMotion,
  isPrimary,
  onActivateTab,
  onCloseTab,
  onAddTab,
  onConfigChange,
  onSplit,
  onCloseGroup,
  onSplitSizes,
  onLoadPreset,
  onResetLayout,
  onCopyUrl,
}: RendererProps) {
  if (tree.kind === 'tabs') {
    return (
      <TabGroup
        group={tree}
        projectId={projectId}
        projectName={projectName}
        totalGroups={totalGroups}
        reducedMotion={reducedMotion}
        isPrimary={isPrimary}
        onActivateTab={onActivateTab}
        onCloseTab={onCloseTab}
        onAddTab={onAddTab}
        onConfigChange={onConfigChange}
        onSplit={onSplit}
        onCloseGroup={onCloseGroup}
        onLoadPreset={onLoadPreset}
        onResetLayout={onResetLayout}
        onCopyUrl={onCopyUrl}
      />
    )
  }
  const childIds = tree.children.map((c) => c.id)
  return (
    <Group
      orientation={tree.orientation}
      id={tree.id}
      onLayoutChanged={(layout: Layout) => {
        const sizes = childIds.map((id) => layout[id] ?? 0)
        if (sizes.every((s) => s > 0)) {
          onSplitSizes(tree.id, sizes)
        }
      }}
      style={{
        display: 'flex',
        flexDirection: tree.orientation === 'horizontal' ? 'row' : 'column',
        height: '100%',
        width: '100%',
      }}
    >
      {tree.children.map((child, i) => (
        <Fragment key={child.id}>
          {i > 0 && (
            <Separator
              // Phase 1 / Phase 3: separators land on the warm bg-2
              // hierarchy and turn pink (accent) on hover so resizes
              // are obvious without a thick handle.
              className={
                tree.orientation === 'horizontal'
                  ? 'w-1 bg-gray-700/40 hover:bg-accent-500 transition-colors cursor-col-resize'
                  : 'h-1 bg-gray-700/40 hover:bg-accent-500 transition-colors cursor-row-resize'
              }
            />
          )}
          <Panel id={child.id} defaultSize={tree.sizes[i]} minSize={10}>
            <Renderer
              tree={child}
              projectId={projectId}
              projectName={projectName}
              totalGroups={totalGroups}
              reducedMotion={reducedMotion}
              // Only the very first child of the very first split owns
              // the page-level actions; every other group renders the
              // standard ⋮ menu without Layout / Reset / Copy URL.
              isPrimary={isPrimary && i === 0}
              onActivateTab={onActivateTab}
              onCloseTab={onCloseTab}
              onAddTab={onAddTab}
              onConfigChange={onConfigChange}
              onSplit={onSplit}
              onCloseGroup={onCloseGroup}
              onSplitSizes={onSplitSizes}
              onLoadPreset={onLoadPreset}
              onResetLayout={onResetLayout}
              onCopyUrl={onCopyUrl}
            />
          </Panel>
        </Fragment>
      ))}
    </Group>
  )
}

// ── Helpers ───────────────────────────────────────────────────

/** A registry of tab-strip rects keyed by groupId. Populated by each
 *  TabGroup on mount via ``registerTabStrip`` and consulted at drag
 *  time to compute the insertion index for tabify drops.
 *
 *  We use a module-level Map rather than React state because the
 *  data is purely a side-channel and re-rendering the whole tree on
 *  every layout-rect change would be wasteful. */
const tabStripRegistry = new Map<
  string,
  { container: HTMLElement; tabs: () => HTMLElement[] }
>()

export function registerTabStrip(
  groupId: string,
  container: HTMLElement,
  tabs: () => HTMLElement[],
): () => void {
  tabStripRegistry.set(groupId, { container, tabs })
  return () => {
    if (tabStripRegistry.get(groupId)?.container === container) {
      tabStripRegistry.delete(groupId)
    }
  }
}

function computeInsertIndex(groupId: string, pointerX: number): number {
  const reg = tabStripRegistry.get(groupId)
  if (!reg) return 0
  const tabRects = reg.tabs().map((el) => {
    const r = el.getBoundingClientRect()
    return { left: r.left, right: r.right }
  })
  return classifyTabInsertIndex(pointerX, tabRects)
}
