import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Plus,
  X,
  MoreVertical,
  SplitSquareVertical,
  SplitSquareHorizontal,
  Trash2,
  Link2,
  RefreshCcw,
  Layers,
} from 'lucide-react'
import { useDraggable, useDroppable } from '@dnd-kit/core'
import type { TabsNode, Pane, PaneType } from './types'
import { MAX_TABS_PER_GROUP, MAX_TAB_GROUPS } from './types'
import { isKeepAlivePane, PANE_TYPE_LABELS } from './paneRegistry'
import { PRESETS } from './presets'
import PaneFrame from './PaneFrame'
import DropZoneOverlay from './DropZoneOverlay'
import {
  dragId,
  groupDropId,
  useDragState,
} from './dndContext'
import { registerTabStrip } from './WorkbenchLayout'

interface Props {
  group: TabsNode
  projectId: string
  /** Phase 3: project name shown in the breadcrumb on the primary
   *  TabGroup (so the rail-collapsed sidebar still surfaces context). */
  projectName: string
  totalGroups: number
  reducedMotion: boolean
  /** Phase 3: only the primary tab group exposes the page-level actions
   *  (Layout preset / Reset / Copy URL) and the projects breadcrumb.
   *  Splits beyond the first child render the plain group menu. */
  isPrimary: boolean

  onActivateTab: (groupId: string, tabId: string) => void
  onCloseTab: (groupId: string, tabId: string) => void
  /** Add a new tab. ``paneType`` is chosen explicitly via the +
   *  dropdown (task 69edb607). The previous "same as active tab"
   *  auto-pick was dropped along with the ⋮ "Change type" submenu. */
  onAddTab: (groupId: string, paneType: PaneType) => void
  onConfigChange: (paneId: string, patch: Record<string, unknown>) => void
  onSplit: (groupId: string, orientation: 'horizontal' | 'vertical') => void
  onCloseGroup: (groupId: string) => void
  onLoadPreset: (presetId: string) => void
  onResetLayout: () => void
  onCopyUrl: () => void
}

const SELECTABLE_TYPES: PaneType[] = [
  'tasks',
  'task-detail',
  'terminal',
  'doc',
  'documents',
  'file-browser',
  'error-tracker',
]

export default function TabGroup({
  group,
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
  onLoadPreset,
  onResetLayout,
  onCopyUrl,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [addMenuOpen, setAddMenuOpen] = useState(false)
  const menuWrapRef = useRef<HTMLDivElement>(null)
  const addMenuWrapRef = useRef<HTMLDivElement>(null)

  // Close ⋮ menu on outside click + ESC.
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null
      if (t && menuWrapRef.current && !menuWrapRef.current.contains(t)) {
        setMenuOpen(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  // Close + (Add tab) menu on outside click + ESC. Mirror of the ⋮
  // menu pattern so behaviour is consistent.
  useEffect(() => {
    if (!addMenuOpen) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node | null
      if (
        t &&
        addMenuWrapRef.current &&
        !addMenuWrapRef.current.contains(t)
      ) {
        setAddMenuOpen(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setAddMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [addMenuOpen])

  const canAddTab = group.tabs.length < MAX_TABS_PER_GROUP
  const canSplit = totalGroups < MAX_TAB_GROUPS

  // Drop target: the entire group rect. dnd-kit only resolves which
  // group is hovered; the layout root narrows that to a 5-zone result
  // using the live pointer position.
  const { setNodeRef: setDropRef } = useDroppable({
    id: groupDropId(group.id),
  })

  // ── Tab strip registration for insert-index computation ─────

  const stripRef = useRef<HTMLDivElement>(null)
  const tabRefs = useRef<Map<string, HTMLElement>>(new Map())

  // useLayoutEffect so the registry has up-to-date refs before the
  // next drag move event fires.
  useLayoutEffect(() => {
    const el = stripRef.current
    if (!el) return
    return registerTabStrip(group.id, el, () => {
      // Return tabs in the current visual order. We read from the
      // ref map but iterate via group.tabs to preserve order even
      // after a reorder.
      const out: HTMLElement[] = []
      for (const t of group.tabs) {
        const r = tabRefs.current.get(t.id)
        if (r) out.push(r)
      }
      return out
    })
  }, [group.id, group.tabs])

  // ── Drag state for overlay rendering ────────────────────────

  const dragState = useDragState()
  const isOverlayActive = dragState.active !== null
  const isThisGroupHovered = dragState.hover?.groupId === group.id
  const activeZone = isThisGroupHovered ? dragState.hover!.zone : null
  const insertIndex =
    isThisGroupHovered && dragState.hover!.zone === 'center'
      ? dragState.hover!.insertIndex
      : -1
  const isSourceGroup = dragState.active?.sourceGroupId === group.id
  const centerDisabled =
    !isSourceGroup && group.tabs.length >= MAX_TABS_PER_GROUP
  const edgesDisabled = totalGroups >= MAX_TAB_GROUPS

  return (
    <div
      ref={setDropRef}
      className="relative flex flex-col h-full min-h-0 bg-gray-900"
    >
      {/* Phase 3: project breadcrumb shown only on the primary tab
          group, so the SidebarRail / mobile state still surface
          context. Hidden on inner splits. */}
      {isPrimary && (
        <div className="flex items-center gap-2 border-b border-gray-700/40 bg-gray-950 px-4 py-1.5 text-[11px] text-gray-300">
          <Link to="/projects" className="hover:text-gray-100">
            projects
          </Link>
          <span aria-hidden className="text-gray-500">
            /
          </span>
          <span className="truncate font-medium text-gray-100" title={projectName}>
            {projectName}
          </span>
        </div>
      )}

      {/* Tab strip */}
      <div className="flex items-stretch h-9 bg-gray-800 border-b border-gray-700/40">
        <div
          ref={stripRef}
          className="relative flex flex-1 min-w-0 overflow-x-auto"
        >
          {group.tabs.map((tab, i) => (
            <DraggableTab
              key={tab.id}
              tab={tab}
              isActive={tab.id === group.activeTabId}
              registerRef={(el) => {
                if (el) tabRefs.current.set(tab.id, el)
                else tabRefs.current.delete(tab.id)
              }}
              onActivate={() => onActivateTab(group.id, tab.id)}
              onClose={() => onCloseTab(group.id, tab.id)}
            >
              {/* Insertion indicator: a thin vertical line at the
                  active drop position drawn between tabs. */}
              {insertIndex === i && <InsertIndicator />}
            </DraggableTab>
          ))}
          {/* Tail insert indicator when dropping after the last tab */}
          {insertIndex === group.tabs.length && <InsertIndicator />}
        </div>

        {/* + (Add tab with type) — moved OUT of the scrolling strip so
            its dropdown is not clipped by ``overflow-x-auto``. The
            dropdown lets the user pick a pane type explicitly,
            replacing the previous "same as active tab" auto-pick and
            the "Change type" submenu in the ⋮ menu. (Task 69edb607.) */}
        <div ref={addMenuWrapRef} className="relative flex items-stretch">
          <button
            type="button"
            onClick={() => {
              if (!canAddTab) return
              setAddMenuOpen((v) => !v)
              setMenuOpen(false)
            }}
            disabled={!canAddTab}
            aria-label={
              canAddTab ? 'Add tab' : `Tab cap reached (${MAX_TABS_PER_GROUP})`
            }
            aria-haspopup="menu"
            aria-expanded={addMenuOpen}
            className="px-2 text-gray-300 hover:text-gray-100 disabled:opacity-40 disabled:cursor-not-allowed"
            title={
              canAddTab ? 'Add tab' : `Tab cap reached (${MAX_TABS_PER_GROUP})`
            }
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
          {addMenuOpen && (
            <div
              role="menu"
              aria-label="Add tab type"
              className="absolute right-0 top-full z-20 mt-1 w-44 rounded-md border border-gray-700/40 bg-gray-800 text-gray-100 shadow-lg text-xs"
            >
              {SELECTABLE_TYPES.map((t) => (
                <button
                  key={t}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setAddMenuOpen(false)
                    onAddTab(group.id, t)
                  }}
                  className="w-full text-left px-3 py-1.5 hover:bg-gray-700/60"
                >
                  {PANE_TYPE_LABELS[t]}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Phase 3: page-level Copy URL — primary group only so it
            sits at the natural top-right corner of the workbench. */}
        {isPrimary && (
          <button
            type="button"
            onClick={onCopyUrl}
            title="現在の Workbench 状態を含む URL をコピー (?task / ?doc / ?view 等)"
            aria-label="URL をコピー"
            className="px-2 text-gray-300 hover:text-gray-100"
          >
            <Link2 className="w-3.5 h-3.5" />
          </button>
        )}

        {/* Group menu — Split / Close. ``Change type`` was removed
            in favour of the + dropdown above (task 69edb607). */}
        <div ref={menuWrapRef} className="relative flex items-stretch">
          <button
            type="button"
            onClick={() => {
              setMenuOpen((v) => !v)
              setAddMenuOpen(false)
            }}
            className="px-2 text-gray-300 hover:text-gray-100"
            aria-label="Pane menu"
          >
            <MoreVertical className="w-4 h-4" />
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 top-full z-20 mt-1 w-56 rounded-md border border-gray-700/40 bg-gray-800 text-gray-100 shadow-lg text-xs"
            >
              {/* Phase 3: page-level actions on the primary group only.
                  Layout preset / Reset are gated this way so secondary
                  splits keep a focused per-group menu. */}
              {isPrimary && (
                <>
                  <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wider text-gray-300">
                    Layout
                  </div>
                  {PRESETS.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => {
                        setMenuOpen(false)
                        onLoadPreset(p.id)
                      }}
                      className="w-full text-left px-3 py-1.5 hover:bg-gray-700/60"
                      title={p.description}
                    >
                      <div className="flex items-center gap-2">
                        <Layers className="w-3.5 h-3.5 text-gray-300" />
                        <span className="font-medium text-gray-50">{p.label}</span>
                      </div>
                      <div className="text-[10px] text-gray-300 mt-0.5 ml-5">
                        {p.description}
                      </div>
                    </button>
                  ))}
                  <MenuItem
                    icon={<RefreshCcw className="w-3.5 h-3.5 text-gray-300" />}
                    label="Reset layout (Cmd+Shift+R)"
                    onClick={() => {
                      setMenuOpen(false)
                      onResetLayout()
                    }}
                  />
                  <div className="border-t border-gray-700/40 my-1" />
                  <div className="px-3 pt-1 pb-1 text-[10px] uppercase tracking-wider text-gray-300">
                    Pane
                  </div>
                </>
              )}
              <MenuItem
                icon={<SplitSquareVertical className="w-3.5 h-3.5" />}
                label="Split right"
                disabled={!canSplit}
                disabledHint={`Max ${MAX_TAB_GROUPS} panes`}
                onClick={() => {
                  setMenuOpen(false)
                  onSplit(group.id, 'horizontal')
                }}
              />
              <MenuItem
                icon={<SplitSquareHorizontal className="w-3.5 h-3.5" />}
                label="Split down"
                disabled={!canSplit}
                disabledHint={`Max ${MAX_TAB_GROUPS} panes`}
                onClick={() => {
                  setMenuOpen(false)
                  onSplit(group.id, 'vertical')
                }}
              />
              <div className="border-t border-gray-700/40 my-1" />
              <MenuItem
                icon={<Trash2 className="w-3.5 h-3.5 text-status-cancel" />}
                label="Close group"
                onClick={() => {
                  setMenuOpen(false)
                  onCloseGroup(group.id)
                }}
              />
            </div>
          )}
        </div>
      </div>

      {/* Active pane body — wrapped so the DnD overlay can sit
          absolutely over it without obscuring the tab strip.
          Keep-alive panes (e.g. ``terminal``) stay mounted across
          tab switches via ``display: none`` so their long-lived
          WebSocket / PTY survive (Invariant L3 / L4). Non-keepAlive
          inactive panes are not rendered (L2). */}
      <div className="relative flex-1 min-h-0">
        {group.tabs.map((tab) => {
          const isActive = tab.id === group.activeTabId
          if (!isActive && !isKeepAlivePane(tab.paneType)) return null
          return (
            <div
              key={tab.id}
              className="absolute inset-0"
              style={isActive ? undefined : { display: 'none' }}
            >
              <PaneFrame
                pane={tab}
                projectId={projectId}
                onConfigChange={onConfigChange}
              />
            </div>
          )
        })}
        <DropZoneOverlay
          active={isOverlayActive}
          activeZone={activeZone}
          edgesDisabled={edgesDisabled}
          centerDisabled={centerDisabled}
          reducedMotion={reducedMotion}
        />
      </div>
    </div>
  )
}

// ── DraggableTab ──────────────────────────────────────────────

interface DraggableTabProps {
  tab: Pane
  isActive: boolean
  registerRef: (el: HTMLElement | null) => void
  onActivate: () => void
  onClose: () => void
  children?: React.ReactNode
}

function DraggableTab({
  tab,
  isActive,
  registerRef,
  onActivate,
  onClose,
  children,
}: DraggableTabProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: dragId(tab.id),
    data: { paneId: tab.id, paneType: tab.paneType },
  })

  // Compose dnd-kit's ref with our local ref for the strip registry.
  const composedRef = (el: HTMLButtonElement | null) => {
    setNodeRef(el)
    registerRef(el)
  }

  // Hide the *source* tab while it's dragging — the DragOverlay
  // renders the moving copy. (We don't fully unmount because the
  // adjacent tabs would reflow and the activator rect would be
  // stale.) Visibility:hidden preserves layout.
  const draggingStyle = isDragging
    ? { opacity: 0, pointerEvents: 'none' as const }
    : undefined

  // ``useEffect`` only to keep ESLint happy about ``registerRef``
  // dependencies; the work happens in the ref callback above.
  useEffect(() => () => registerRef(null), [registerRef])

  return (
    <>
      {children}
      <button
        ref={composedRef}
        {...attributes}
        {...listeners}
        type="button"
        onClick={(e) => {
          // dnd-kit's listeners don't suppress clicks below the
          // activation distance, so this fires for short-distance
          // pointer up events. Treat it as activate.
          if ((e as { defaultPrevented?: boolean }).defaultPrevented) return
          onActivate()
        }}
        onAuxClick={(e) => {
          if (e.button === 1) {
            e.preventDefault()
            onClose()
          }
        }}
        style={{
          ...draggingStyle,
          // Active tab gets a 2px accent (pink) bar across the top, per
          // the design spec (`UI 再設計仕様書 §4.3`). Inline so we don't
          // grow the className blob with conditional pseudo-elements.
          // ``#fc618d`` mirrors ``theme.colors.accent.500``.
          boxShadow: isActive ? 'inset 0 2px 0 0 #fc618d' : undefined,
        }}
        className={`group relative flex items-center gap-1.5 px-3 text-xs border-r border-gray-700/40 max-w-[14rem] flex-shrink-0 select-none cursor-grab active:cursor-grabbing ${
          isActive
            ? 'bg-gray-900 text-gray-50 font-medium'
            : 'text-gray-300 hover:bg-gray-700/60 hover:text-gray-50'
        }`}
        title={PANE_TYPE_LABELS[tab.paneType]}
      >
        <span className="truncate">{PANE_TYPE_LABELS[tab.paneType]}</span>
        <span
          role="button"
          tabIndex={0}
          // Stop the close affordance from initiating a drag —
          // dnd-kit treats *any* pointerdown on the draggable as a
          // potential drag start, which would block the click that
          // follows. Capturing here keeps the listener from seeing
          // the event.
          onPointerDownCapture={(e) => {
            e.stopPropagation()
          }}
          onClick={(e) => {
            e.stopPropagation()
            onClose()
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.stopPropagation()
              onClose()
            }
          }}
          className={`rounded p-0.5 transition-opacity ${
            isActive
              ? 'opacity-60 hover:opacity-100 hover:bg-gray-700/60'
              : 'opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-gray-700/60'
          }`}
          aria-label="Close tab"
        >
          <X className="w-3 h-3" />
        </span>
      </button>
    </>
  )
}

// ── InsertIndicator ───────────────────────────────────────────

function InsertIndicator() {
  return (
    <span
      className="self-stretch w-0.5 bg-accent-500 mx-0 flex-shrink-0"
      aria-hidden
    />
  )
}

// ── MenuItem ──────────────────────────────────────────────────

interface MenuItemProps {
  icon: React.ReactNode
  label: string
  disabled?: boolean
  disabledHint?: string
  onClick: () => void
}

function MenuItem({ icon, label, disabled, disabledHint, onClick }: MenuItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={disabled ? disabledHint : undefined}
      className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-gray-700/60 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent text-gray-100"
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}
