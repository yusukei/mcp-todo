import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ChevronDown, RefreshCcw } from 'lucide-react'
import { api } from '../api/client'
import type { Project } from '../types'
import WorkbenchLayout from '../workbench/WorkbenchLayout'
import {
  loadLayout,
  makeDebouncedSaver,
  saveLayout,
  subscribeCrossTab,
} from '../workbench/storage'
import {
  addTab,
  changePaneType,
  closeTab,
  defaultLayout,
  makePane,
  moveTabToCenter,
  moveTabToEdge,
  setActiveTab,
  setSplitSizes,
  splitTabGroup,
  updatePaneConfig,
  validateTree,
} from '../workbench/treeUtils'
import type { DropEdge } from '../workbench/treeUtils'
import type { LayoutTree, PaneType } from '../workbench/types'
import { KNOWN_PANE_TYPES } from '../workbench/paneRegistry'
import { WorkbenchEventProvider, useWorkbenchEventBus } from '../workbench/eventBus'
import { PRESETS, getPreset } from '../workbench/presets'
import TaskDetail from '../components/task/TaskDetail'
import {
  dfsPanes,
  findGroupIdOf,
  focusIndex,
  focusPaneFrame,
  matchHotkey,
  resolveFocusedPaneId,
} from '../workbench/hotkeys'

interface State {
  tree: LayoutTree
  /** Wall-clock ms when the local tree was last touched. Used to
   *  decide whether to adopt a cross-tab update. */
  localStamp: number
}

type Action =
  | { type: 'replace'; tree: LayoutTree; stamp: number }
  | { type: 'mutate'; next: LayoutTree }

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'replace':
      return { tree: action.tree, localStamp: action.stamp }
    case 'mutate':
      return { tree: action.next, localStamp: Date.now() }
  }
}

export default function WorkbenchPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const { data: project, isLoading } = useQuery<Project>({
    queryKey: ['project', projectId],
    queryFn: () =>
      api.get(`/projects/${projectId}`).then((r) => r.data),
    enabled: !!projectId,
  })

  const [state, dispatch] = useReducer(reducer, undefined as unknown as State, () => ({
    // Hydrated below in the projectId effect.
    tree: defaultLayout(),
    localStamp: 0,
  }))

  const saverRef = useRef(makeDebouncedSaver(300))
  // Keep a ref to the most recently dispatched stamp so cross-tab
  // updates compare against the freshest value without depending on
  // closure capture order.
  const localStampRef = useRef(state.localStamp)
  useEffect(() => {
    localStampRef.current = state.localStamp
  }, [state.localStamp])

  // Initial hydrate when projectId is known.
  useEffect(() => {
    if (!projectId) return
    const tree = loadLayout(projectId, KNOWN_PANE_TYPES)
    dispatch({ type: 'replace', tree, stamp: 0 })
  }, [projectId])

  // Persist layout changes (debounced).
  useEffect(() => {
    if (!projectId || state.localStamp === 0) return
    saverRef.current.save(projectId, state.tree)
  }, [projectId, state.tree, state.localStamp])

  // Flush on unmount so a fast navigation away doesn't lose the
  // last few hundred ms of edits.
  useEffect(() => {
    const saver = saverRef.current
    return () => saver.flush()
  }, [])

  // Cross-tab sync: another tab on the same project saved a newer
  // tree → replace our local state. Last-write-wins.
  useEffect(() => {
    if (!projectId) return
    return subscribeCrossTab(projectId, KNOWN_PANE_TYPES, (tree, stamp) => {
      if (stamp > localStampRef.current) {
        dispatch({ type: 'replace', tree, stamp })
      }
    })
  }, [projectId])

  const updateTree = useCallback(
    (next: LayoutTree) => {
      // Defensive: validate the candidate tree before dispatching so a
      // bug in treeUtils never persists a structurally invalid layout
      // that would lock the user out on next reload.
      const err = validateTree(next)
      if (err) {
        // eslint-disable-next-line no-console
        console.error('[Workbench] refusing to apply invalid tree:', err)
        return
      }
      dispatch({ type: 'mutate', next })
    },
    [],
  )

  // ── Layout mutators ────────────────────────────────────────

  const onActivateTab = useCallback(
    (groupId: string, tabId: string) =>
      updateTree(setActiveTab(state.tree, groupId, tabId)),
    [state.tree, updateTree],
  )
  const onCloseTab = useCallback(
    (groupId: string, tabId: string) => {
      const next = closeTab(state.tree, groupId, tabId)
      updateTree(next ?? defaultLayout())
    },
    [state.tree, updateTree],
  )
  const onAddTab = useCallback(
    (groupId: string, paneType: PaneType) =>
      updateTree(addTab(state.tree, groupId, makePane(paneType))),
    [state.tree, updateTree],
  )
  const onChangePaneType = useCallback(
    (paneId: string, paneType: PaneType) =>
      updateTree(changePaneType(state.tree, paneId, paneType)),
    [state.tree, updateTree],
  )
  const onConfigChange = useCallback(
    (paneId: string, patch: Record<string, unknown>) =>
      updateTree(updatePaneConfig(state.tree, paneId, patch)),
    [state.tree, updateTree],
  )
  const onSplit = useCallback(
    (groupId: string, orientation: 'horizontal' | 'vertical') =>
      updateTree(splitTabGroup(state.tree, groupId, orientation, 'tasks')),
    [state.tree, updateTree],
  )
  const onCloseGroup = useCallback(
    (groupId: string) => {
      // Walk the group's tabs and close them one by one. For a
      // single-tab group this is one closeTab call; for a multi-tab
      // group it ends with collapsing the now-empty group.
      let next = state.tree
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const collectIds = (t: any): string[] => {
        if (t.kind === 'tabs' && t.id === groupId) return t.tabs.map((p: { id: string }) => p.id)
        if (t.kind === 'split') return t.children.flatMap(collectIds)
        return []
      }
      const tabIds = collectIds(state.tree)
      for (const id of tabIds) {
        const r = closeTab(next, groupId, id)
        next = r ?? defaultLayout()
      }
      updateTree(next)
    },
    [state.tree, updateTree],
  )
  const onSplitSizes = useCallback(
    (splitId: string, sizes: number[]) =>
      updateTree(setSplitSizes(state.tree, splitId, sizes)),
    [state.tree, updateTree],
  )
  const onMoveTab = useCallback(
    (
      paneId: string,
      targetGroupId: string,
      drop: { kind: 'edge'; edge: DropEdge } | { kind: 'center'; index: number },
    ) => {
      const next =
        drop.kind === 'edge'
          ? moveTabToEdge(state.tree, paneId, targetGroupId, drop.edge)
          : moveTabToCenter(state.tree, paneId, targetGroupId, drop.index)
      // ``moveTabTo*`` returns the original tree on cap / not-found —
      // no-op skip avoids stamping a fresh ``localStamp`` for nothing.
      if (next === state.tree) return
      updateTree(next)
    },
    [state.tree, updateTree],
  )

  // ── Preset / reset ──────────────────────────────────────────

  const [confirmReset, setConfirmReset] = useState<
    { presetId: string } | null
  >(null)

  const applyPreset = useCallback(
    (presetId: string) => {
      const preset = getPreset(presetId)
      if (!preset) return
      updateTree(preset.build())
    },
    [updateTree],
  )

  // ── Keyboard shortcuts ──────────────────────────────────────
  //
  // Refs over closures so the listener registered once on mount
  // always sees the freshest tree + dispatchers. Re-registering the
  // listener on every render would deadlock with the focus-restore
  // call inside the handlers.
  const treeRef = useRef(state.tree)
  useEffect(() => {
    treeRef.current = state.tree
  }, [state.tree])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const hk = matchHotkey(e)
      if (!hk) return
      const tree = treeRef.current
      const focusedPaneId = resolveFocusedPaneId()

      if (hk === 'reset-layout') {
        e.preventDefault()
        setConfirmReset({ presetId: 'tasks-only' })
        return
      }

      const fIdx = focusIndex(hk)
      if (fIdx !== null) {
        e.preventDefault()
        const panes = dfsPanes(tree)
        const target = panes[fIdx - 1]
        if (!target) return
        // Activate its tab (so it's mounted) then move keyboard focus.
        const groupId = findGroupIdOf(tree, target.id)
        if (groupId) {
          updateTree(setActiveTab(tree, groupId, target.id))
        }
        // requestAnimationFrame so the activated tab has rendered
        // before we attempt to focus its DOM node.
        window.requestAnimationFrame(() => focusPaneFrame(target.id))
        return
      }

      // The remaining shortcuts need a focused pane.
      if (!focusedPaneId) return
      const groupId = findGroupIdOf(tree, focusedPaneId)
      if (!groupId) return

      if (hk === 'close-pane') {
        e.preventDefault()
        const next = closeTab(tree, groupId, focusedPaneId)
        updateTree(next ?? defaultLayout())
        return
      }
      if (hk === 'split-vertical' || hk === 'split-horizontal') {
        e.preventDefault()
        // Re-use the same pane type as the focused tab so the new
        // pane lands in a familiar context.
        const focusedPane = dfsPanes(tree).find((p) => p.id === focusedPaneId)
        const orientation =
          hk === 'split-vertical' ? 'vertical' : 'horizontal'
        const next = splitTabGroup(
          tree,
          groupId,
          orientation,
          focusedPane?.paneType ?? 'tasks',
        )
        if (next === tree) return
        updateTree(next)
        return
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [updateTree])

  // Force-flush on route change so the next route load sees the
  // current state. (saveLayout is synchronous w.r.t. localStorage.)
  useEffect(() => {
    if (!projectId) return
    return () => {
      saverRef.current.flush()
      // Belt-and-suspenders: ensure we wrote the last state even if
      // the debounced save was cancelled mid-flight.
      if (localStampRef.current > 0) {
        saveLayout(projectId, state.tree)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const projectName = useMemo(
    () => project?.name ?? projectId ?? '...',
    [project, projectId],
  )

  if (!projectId) {
    return <div className="p-8 text-gray-400">Invalid project id.</div>
  }
  if (isLoading && !project) {
    return <div className="p-8 text-gray-400">Loading project…</div>
  }

  return (
    <div className="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        {/* Header back: deep-link friendly fixed target (Decision §5.5.5).
             We deliberately NOT use navigate(-1) — a deep-link
             arrival (new tab on /projects/:id?task=...) has an empty
             history stack. Hard-coding `/projects` always works. */}
        <Link
          to="/projects"
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          projects
        </Link>
        <span className="text-gray-400">/</span>
        <span
          className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate max-w-[16rem]"
          title={projectName}
        >
          {projectName}
        </span>
        <PresetMenu
          onPick={(id) => setConfirmReset({ presetId: id })}
        />
        <button
          type="button"
          onClick={() => setConfirmReset({ presetId: 'tasks-only' })}
          className="ml-auto flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
          title="Reset layout (Cmd/Ctrl+Shift+R)"
        >
          <RefreshCcw className="w-3.5 h-3.5" />
          Reset
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <WorkbenchEventProvider tree={state.tree}>
          <WorkbenchLayout
            tree={state.tree}
            projectId={projectId}
            onActivateTab={onActivateTab}
            onCloseTab={onCloseTab}
            onAddTab={onAddTab}
            onChangePaneType={onChangePaneType}
            onConfigChange={onConfigChange}
            onSplit={onSplit}
            onCloseGroup={onCloseGroup}
            onSplitSizes={onSplitSizes}
            onMoveTab={onMoveTab}
          />
          <WorkbenchFallbacks projectId={projectId} />
        </WorkbenchEventProvider>
      </div>

      {confirmReset && (
        <ResetConfirmModal
          presetLabel={
            getPreset(confirmReset.presetId)?.label ?? 'the selected preset'
          }
          onCancel={() => setConfirmReset(null)}
          onConfirm={() => {
            applyPreset(confirmReset.presetId)
            setConfirmReset(null)
          }}
        />
      )}
    </div>
  )
}

// ── Preset menu ────────────────────────────────────────────────

function PresetMenu({ onPick }: { onPick: (presetId: string) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-2 py-0.5 rounded border border-transparent hover:border-gray-200 dark:hover:border-gray-700"
        title="Load a preset layout (replaces the current layout)"
      >
        Layout
        <ChevronDown className="w-3 h-3" />
      </button>
      {open && (
        <div
          className="absolute left-0 top-full z-30 mt-1 w-64 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg text-xs"
          onMouseLeave={() => setOpen(false)}
        >
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => {
                setOpen(false)
                onPick(p.id)
              }}
              className="w-full text-left px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700"
              title={p.description}
            >
              <div className="font-medium text-gray-800 dark:text-gray-200">
                {p.label}
              </div>
              <div className="text-[10px] text-gray-500 dark:text-gray-400 mt-0.5">
                {p.description}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Fallback slide-overs (Decision D1) ────────────────────────
//
// When a cross-pane event has no matching pane in the layout (e.g.
// the user clicks a task in TasksPane but no TaskDetailPane exists),
// the bus calls a registered fallback. WorkbenchFallbacks owns the
// slide-over UI for those events. Lives inside WorkbenchEventProvider
// so it can call `bus.setFallback(...)` from a useEffect.

function WorkbenchFallbacks({ projectId }: { projectId: string }) {
  const bus = useWorkbenchEventBus()
  const [taskFallbackId, setTaskFallbackId] = useState<string | null>(null)

  useEffect(() => {
    return bus.setFallback('open-task', ({ taskId }) => {
      setTaskFallbackId(taskId)
    })
  }, [bus])

  if (!taskFallbackId) return null
  return (
    <TaskDetail
      key={taskFallbackId}
      taskId={taskFallbackId}
      projectId={projectId}
      onClose={() => setTaskFallbackId(null)}
      onNavigateTask={(next) => setTaskFallbackId(next)}
      // Slide-over (legacy modal) — used when no TaskDetailPane is
      // in the layout.
      displayMode="slideOver"
    />
  )
}

// ── Reset / load-preset confirmation modal ─────────────────────

interface ResetConfirmModalProps {
  presetLabel: string
  onCancel: () => void
  onConfirm: () => void
}

function ResetConfirmModal({
  presetLabel,
  onCancel,
  onConfirm,
}: ResetConfirmModalProps) {
  // ESC to dismiss / Enter to confirm — the modal is the only
  // foreground element so we capture both at the document level.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
      if (e.key === 'Enter') onConfirm()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onCancel, onConfirm])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl p-5 max-w-md w-full mx-4">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
          Replace current layout?
        </h2>
        <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
          Loading <span className="font-medium">{presetLabel}</span> will
          discard your current Workbench arrangement (tab positions,
          splits, per-pane configs). Per-pane data on the server (tasks,
          documents, terminal sessions) is unaffected.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1.5 text-xs rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white"
            autoFocus
          >
            Replace layout
          </button>
        </div>
      </div>
    </div>
  )
}
