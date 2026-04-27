import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Project } from '../types'
import WorkbenchLayout from '../workbench/WorkbenchLayout'
import {
  getOrCreateClientId,
  loadLayout,
  makeDebouncedSaver,
  saveLayout,
  subscribeCrossTab,
} from '../workbench/storage'
import {
  beaconLayout,
  getServerLayout,
  makeServerSaver,
} from '../api/workbenchLayouts'
import { LAYOUT_SCHEMA_VERSION } from '../workbench/types'
import {
  addTab,
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
import type { LayoutTree, Pane, PaneType } from '../workbench/types'
import { KNOWN_PANE_TYPES } from '../workbench/paneRegistry'
import { WorkbenchEventProvider, useWorkbenchEventBus } from '../workbench/eventBus'
import { getPreset } from '../workbench/presets'
import {
  findFirstPaneOfType,
  findFirstTabsNodeId,
  parseUrlContract,
  searchParamsEqual,
  serialiseUrlContract,
  type ViewName,
} from '../workbench/urlContract'
import { showInfoToast, showSuccessToast } from '../components/common/Toast'
import TaskDetail from '../components/task/TaskDetail'
import { projectsApi } from '../api/projects'
import { Sparkle } from 'lucide-react'
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
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
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

  // Slide-over fallback for `?task=` when no task-detail pane exists
  // in the layout (Decision D1). Lifted so the URL-hydrate effect
  // can write to it; WorkbenchFallbacks subscribes to display.
  const [taskFallbackId, setTaskFallbackId] = useState<string | null>(null)

  const saverRef = useRef(makeDebouncedSaver(300))

  // ── Server-side layout sync (Phase B) ─────────────────────────
  // Per-tab identifier so SSE echoes from this tab can be skipped.
  const clientIdRef = useRef<string>(getOrCreateClientId())
  // updated_at of the most recent value we either fetched from or
  // wrote to the server. New server payloads with the same timestamp
  // are our own echoes (or someone else writing the identical body)
  // and don't require a re-replace.
  const lastServerStampRef = useRef<string | null>(null)
  // One-shot guard for the initial server payload arriving AFTER
  // the user has already started editing the local tree. Without
  // this, the late ``serverLayout`` Effect dispatches `replace`
  // with the stale server snapshot and the user's just-added
  // tab vanishes ("tab generated, then immediately closes").
  const hasAdoptedInitialServerLayoutRef = useRef(false)
  // Debounced server PUT — independent from the localStorage saver
  // so a slow network never delays the optimistic local cache.
  const serverSaverRef = useRef(
    makeServerSaver(500, () => clientIdRef.current, (ts) => {
      lastServerStampRef.current = ts
    }),
  )

  const { data: serverLayout } = useQuery({
    queryKey: ['workbench-layout', projectId],
    queryFn: () => getServerLayout(projectId!),
    enabled: !!projectId,
    // SSE-driven invalidation is the only refetch trigger. Without
    // ``staleTime: Infinity`` the focus / mount refetch would race
    // the user's in-flight edits and clobber them.
    staleTime: Infinity,
    retry: false,
  })
  // Keep a ref to the most recently dispatched stamp so cross-tab
  // updates compare against the freshest value without depending on
  // closure capture order.
  const localStampRef = useRef(state.localStamp)
  useEffect(() => {
    localStampRef.current = state.localStamp
  }, [state.localStamp])

  // Initial hydrate when projectId is known. Layered as:
  //   localStorage layout → ?layout= preset (one-shot) → ?task= /
  //   ?doc= / ?view= seed values → legacy ?view=docs/files/errors
  //   compat (Decision D4).
  // URL is the source of truth for paneConfig fields it covers
  // (Plan v2.4 §5.5.4).
  useEffect(() => {
    if (!projectId) return
    let tree = loadLayout(projectId, KNOWN_PANE_TYPES)
    const url = parseUrlContract(searchParams)

    // ?layout=<presetId> overrides the hydrated layout (one-shot,
    // not persisted). Unknown id → console.warn + ignore.
    if (url.layout) {
      const preset = getPreset(url.layout)
      if (preset) tree = preset.build()
      else
        // eslint-disable-next-line no-console
        console.warn(`[Workbench] unknown ?layout= preset: ${url.layout}`)
    }

    // Legacy ?view=docs/files/errors → add the pane (Decision D4).
    // Only if the layout doesn't already include one of that type
    // (avoid duplicate after the first reload).
    if (url.legacyViewToAdd) {
      const existing = findFirstPaneOfType(tree, url.legacyViewToAdd)
      if (!existing) {
        const targetGroupId = findFirstTabsNodeId(tree)
        if (targetGroupId) {
          tree = addTab(tree, targetGroupId, makePane(url.legacyViewToAdd))
          showInfoToast(
            `URL の ?view=${url.legacyViewToAdd} は廃止されました。次回からは Layout メニューから pane を追加してください。`,
          )
        }
      }
    }

    // ?view= → first tasks pane viewMode
    if (url.view) {
      const tasksPane = findFirstPaneOfType(tree, 'tasks')
      if (tasksPane) {
        tree = updatePaneConfig(tree, tasksPane.id, { viewMode: url.view })
      }
    }

    // ?task= → first task-detail pane, or slide-over fallback
    if (url.task) {
      const detailPane = findFirstPaneOfType(tree, 'task-detail')
      if (detailPane) {
        tree = updatePaneConfig(tree, detailPane.id, { taskId: url.task })
      } else {
        setTaskFallbackId(url.task)
      }
    }

    // ?doc= → first doc pane (no slide-over fallback for docs in
    // Phase C2; the user can add a DocPane from + menu)
    if (url.doc) {
      const docPane = findFirstPaneOfType(tree, 'doc')
      if (docPane) {
        tree = updatePaneConfig(tree, docPane.id, { docId: url.doc })
      }
    }

    if (url.hadUnknownValue) {
      // eslint-disable-next-line no-console
      console.warn(
        '[Workbench] URL contained unknown query value(s); using defaults',
      )
    }

    dispatch({ type: 'replace', tree, stamp: 0 })
    // We deliberately do NOT depend on `searchParams` — this is a
    // mount-time hydration. Subsequent URL changes from history pop
    // are handled by re-running this effect via the projectId key
    // (which doesn't change), so we'd need to add searchParams to
    // deps for full back/forward support. That refinement is a
    // polish item; the common deep-link flow (initial load) works
    // with mount-only hydration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  // Persist layout changes — local cache (immediate-ish) + server
  // (debounced). Both run on every state mutation so an offline tab
  // continues to feel responsive while the server save retries.
  useEffect(() => {
    if (!projectId || state.localStamp === 0) return
    saverRef.current.save(projectId, state.tree)
    serverSaverRef.current.save(projectId, state.tree)
  }, [projectId, state.tree, state.localStamp])

  // Flush on unmount so a fast navigation away doesn't lose the
  // last few hundred ms of edits.
  useEffect(() => {
    const saver = saverRef.current
    const server = serverSaverRef.current
    return () => {
      saver.flush()
      server.flush()
    }
  }, [])

  // Adopt server layout when it arrives. Skip when:
  //   - It's our own echo (matching client_id), OR
  //   - The server timestamp is unchanged from the last value we
  //     observed (handles the initial load → server save → echo
  //     loop without flicker).
  useEffect(() => {
    if (!projectId || !serverLayout) return
    if (serverLayout.client_id === clientIdRef.current) {
      lastServerStampRef.current = serverLayout.updated_at
      hasAdoptedInitialServerLayoutRef.current = true
      return
    }
    if (serverLayout.updated_at === lastServerStampRef.current) return
    // Race guard: if the user has already mutated the local tree
    // before the first server snapshot arrived, treat the
    // snapshot as stale relative to the in-memory edits and skip
    // adopting it. Subsequent SSE updates compare strictly newer
    // stamps via lastServerStampRef.
    if (
      !hasAdoptedInitialServerLayoutRef.current &&
      state.localStamp > 0
    ) {
      hasAdoptedInitialServerLayoutRef.current = true
      lastServerStampRef.current = serverLayout.updated_at
      return
    }
    hasAdoptedInitialServerLayoutRef.current = true
    lastServerStampRef.current = serverLayout.updated_at
    serverSaverRef.current.cancel()
    dispatch({ type: 'replace', tree: serverLayout.tree, stamp: 0 })
    saveLayout(projectId, serverLayout.tree)
  }, [projectId, serverLayout, state.localStamp])

  // Best-effort flush on tab close / hide. ``visibilitychange`` is
  // the modern, mobile-friendly trigger; ``pagehide`` covers cases
  // where the tab is bfcache-discarded without ``beforeunload``.
  // Both call ``beaconLayout`` because either path may be the *only*
  // signal we get before the tab is gone, depending on the browser.
  useEffect(() => {
    if (!projectId) return
    const flushNow = () => {
      // Skip when the user has not modified anything yet — the saved
      // copy on the server is already authoritative.
      if (state.localStamp === 0) return
      serverSaverRef.current.cancel()
      beaconLayout(projectId, {
        tree: state.tree,
        schema_version: LAYOUT_SCHEMA_VERSION,
        client_id: clientIdRef.current,
      })
    }
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') flushNow()
    }
    const onPageHide = () => flushNow()
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', onPageHide)
    }
  }, [projectId, state.tree, state.localStamp])

  // Cross-tab SSE invalidation hook: useSSE invalidates this query
  // when ``workbench.layout.updated`` arrives, so nothing else is
  // needed here — the ``serverLayout`` effect above does the merge.
  // We retain ``queryClient`` as a dep marker so the effect picks up
  // a fresh client across HMR cycles.
  void queryClient

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

  // State → URL writeback (Plan v2.4 §5.5.3 — selection = replace).
  // Watches the layout for changes to the *first* pane of each
  // synced type and mirrors its config to URL params. The
  // `searchParamsEqual` guard short-circuits the effect when the URL
  // already matches, preventing a re-render loop with the URL → state
  // hydrate effect.
  //
  // This intentionally tracks the *first* pane (DFS order) rather
  // than the focused one — focus tracking lives in the bus and would
  // add render churn here. Plan §5.5.3 "focus 移動だけで URL は書き
  // 換わらない" is satisfied because click-driven paneConfig changes
  // surface here, but raw focus moves don't touch paneConfig.
  useEffect(() => {
    if (!projectId || state.localStamp === 0) return
    const detailPane = findFirstPaneOfType(state.tree, 'task-detail')
    const docPane = findFirstPaneOfType(state.tree, 'doc')
    const tasksPane = findFirstPaneOfType(state.tree, 'tasks')
    const desiredTask =
      (detailPane?.paneConfig as { taskId?: string } | undefined)?.taskId ?? null
    const desiredDoc =
      (docPane?.paneConfig as { docId?: string } | undefined)?.docId ?? null
    const rawView = (tasksPane?.paneConfig as { viewMode?: string } | undefined)?.viewMode
    // Omit `?view=` when board (the implicit default) so common URLs
    // stay clean — only deviations end up in the bar.
    const desiredView: ViewName | null =
      rawView === 'list' || rawView === 'timeline' ? rawView : null
    const next = serialiseUrlContract(searchParams, {
      task: desiredTask,
      doc: desiredDoc,
      view: desiredView,
    })
    if (!searchParamsEqual(searchParams, next)) {
      setSearchParams(next, { replace: true })
    }
  }, [projectId, state.tree, state.localStamp, searchParams, setSearchParams])

  // Hoisted above ``updateTree`` so the short-circuit below can
  // read the current tree without going through state. Updated
  // during render (not in a useEffect) so child useEffects
  // observe the latest tree synchronously.
  const treeRef = useRef(state.tree)
  treeRef.current = state.tree

  const updateTree = useCallback(
    (next: LayoutTree) => {
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
  //
  // All mutators read the *latest* tree via ``treeRef.current`` and
  // depend only on ``updateTree`` so their identities stay stable
  // across ``state.tree`` changes (Invariant C1). A re-created
  // callback would otherwise propagate down to ``TerminalView``
  // whose useEffect deps include the callback, tearing down the
  // WebSocket on every layout edit.

  const onActivateTab = useCallback(
    (groupId: string, tabId: string) =>
      updateTree(setActiveTab(treeRef.current, groupId, tabId)),
    [updateTree],
  )
  const onCloseTab = useCallback(
    (groupId: string, tabId: string) => {
      const next = closeTab(treeRef.current, groupId, tabId)
      updateTree(next ?? defaultLayout())
    },
    [updateTree],
  )
  const onAddTab = useCallback(
    (groupId: string, paneType: PaneType) =>
      updateTree(addTab(treeRef.current, groupId, makePane(paneType))),
    [updateTree],
  )
  const onConfigChange = useCallback(
    (paneId: string, patch: Record<string, unknown>) =>
      updateTree(updatePaneConfig(treeRef.current, paneId, patch)),
    [updateTree],
  )
  const onSplit = useCallback(
    (groupId: string, orientation: 'horizontal' | 'vertical') =>
      updateTree(splitTabGroup(treeRef.current, groupId, orientation, 'tasks')),
    [updateTree],
  )
  const onCloseGroup = useCallback(
    (groupId: string) => {
      // Walk the group's tabs and close them one by one. For a
      // single-tab group this is one closeTab call; for a multi-tab
      // group it ends with collapsing the now-empty group.
      const startTree = treeRef.current
      let next = startTree
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const collectIds = (t: any): string[] => {
        if (t.kind === 'tabs' && t.id === groupId) return t.tabs.map((p: { id: string }) => p.id)
        if (t.kind === 'split') return t.children.flatMap(collectIds)
        return []
      }
      const tabIds = collectIds(startTree)
      for (const id of tabIds) {
        const r = closeTab(next, groupId, id)
        next = r ?? defaultLayout()
      }
      updateTree(next)
    },
    [updateTree],
  )
  const onSplitSizes = useCallback(
    (splitId: string, sizes: number[]) =>
      updateTree(setSplitSizes(treeRef.current, splitId, sizes)),
    [updateTree],
  )
  const onMoveTab = useCallback(
    (
      paneId: string,
      targetGroupId: string,
      drop: { kind: 'edge'; edge: DropEdge } | { kind: 'center'; index: number },
    ) => {
      const t = treeRef.current
      const next =
        drop.kind === 'edge'
          ? moveTabToEdge(t, paneId, targetGroupId, drop.edge)
          : moveTabToCenter(t, paneId, targetGroupId, drop.index)
      // ``moveTabTo*`` returns the original tree on cap / not-found —
      // no-op skip avoids stamping a fresh ``localStamp`` for nothing.
      if (next === t) return
      updateTree(next)
    },
    [updateTree],
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
  // call inside the handlers. ``treeRef`` is hoisted above the
  // mutators so they can share it.
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

  // Phase 3: page-level actions previously surfaced by the deleted
  // header strip — now plumbed into TabGroup's primary ⋮ menu / icon.
  // Declared *before* the early returns so the hook order stays stable
  // across loading / loaded states (React's rules-of-hooks).
  const onCopyUrl = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(window.location.href)
      showSuccessToast('URL をクリップボードにコピーしました')
    } catch {
      showInfoToast('クリップボードへのアクセスに失敗しました')
    }
  }, [])

  const onResetLayout = useCallback(() => {
    setConfirmReset({ presetId: 'tasks-only' })
  }, [])

  const onLoadPreset = useCallback((presetId: string) => {
    setConfirmReset({ presetId })
  }, [])

  if (!projectId) {
    return <div className="p-8 text-gray-400">Invalid project id.</div>
  }
  if (isLoading && !project) {
    return <div className="p-8 text-gray-400">Loading project…</div>
  }

  return (
    // P2-F: paper-grain で Workbench メイン領域に微弱な紙質感ノイズを乗せる。
    // tokens.css と同じ terra (pink) + decision (purple) の radial gradient
    // + SVG turbulence overlay。relative + overflow-hidden が前提。
    <div className="paper-grain relative flex flex-col h-full overflow-hidden bg-gray-900">
      {/* Phase 3: top header strip removed. Project breadcrumb and
          page-level actions live inside the primary TabGroup. */}
      <div className="flex-1 min-h-0">
        <WorkbenchEventProvider tree={state.tree}>
          <WorkbenchLayout
            tree={state.tree}
            projectId={projectId}
            projectName={projectName}
            onActivateTab={onActivateTab}
            onCloseTab={onCloseTab}
            onAddTab={onAddTab}
            onConfigChange={onConfigChange}
            onSplit={onSplit}
            onCloseGroup={onCloseGroup}
            onSplitSizes={onSplitSizes}
            onMoveTab={onMoveTab}
            onLoadPreset={onLoadPreset}
            onResetLayout={onResetLayout}
            onCopyUrl={onCopyUrl}
          />
          <WorkbenchFallbacks
            projectId={projectId}
            taskFallbackId={taskFallbackId}
            setTaskFallbackId={setTaskFallbackId}
          />
        </WorkbenchEventProvider>
      </div>

      {/* P0-1: 「AI が n 件作業中」FAB — 設計プロト variant-b.jsx の
          position:absolute; bottom:18; right:22。pulse は status-dot
          .in_progress クラスで取得。stats:today を所有するクエリは
          SidebarFull と共有 (React Query キャッシュ済み)。 */}
      <ActiveAiPill projectId={projectId} />

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

// ── Fallback slide-overs (Decision D1) ────────────────────────
//
// When a cross-pane event has no matching pane in the layout (e.g.
// the user clicks a task in TasksPane but no TaskDetailPane exists),
// the bus calls a registered fallback. WorkbenchFallbacks owns the
// slide-over UI for those events. Lives inside WorkbenchEventProvider
// so it can call `bus.setFallback(...)` from a useEffect.

interface WorkbenchFallbacksProps {
  projectId: string
  taskFallbackId: string | null
  setTaskFallbackId: (id: string | null) => void
}

function WorkbenchFallbacks({
  projectId,
  taskFallbackId,
  setTaskFallbackId,
}: WorkbenchFallbacksProps) {
  const bus = useWorkbenchEventBus()

  useEffect(() => {
    return bus.setFallback('open-task', ({ taskId }) => {
      setTaskFallbackId(taskId)
    })
  }, [bus, setTaskFallbackId])

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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-gray-800 border border-line-2 rounded-lg shadow-xl p-5 max-w-md w-full mx-4">
        <h2 className="font-serif text-base font-semibold text-gray-50">
          Replace current layout?
        </h2>
        <p className="mt-2 text-sm text-gray-100">
          Loading <span className="font-medium">{presetLabel}</span> will
          discard your current Workbench arrangement (tab positions,
          splits, per-pane configs). Per-pane data on the server (tasks,
          documents, terminal sessions) is unaffected.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1.5 text-xs rounded border border-line-2 text-gray-100 hover:bg-gray-700/60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="px-3 py-1.5 text-xs rounded bg-accent-500 hover:bg-accent-400 text-white"
            autoFocus
          >
            Replace layout
          </button>
        </div>
      </div>
    </div>
  )
}

// ── ActiveAiPill (P0-1 FAB) ───────────────────────────────────
//
// 設計プロト variant-b.jsx:50-53 の右下 FAB。
//   position:absolute; bottom:18; right:22;
//   padding: 8px 14px; borderRadius: 999;
//   background: var(--terra-glow); border: 1px solid var(--terra-2);
//   backdropFilter: blur(8px);
// 進行中タスク数 (stats.in_progress) > 0 のときだけ表示。
function ActiveAiPill({ projectId }: { projectId: string }) {
  const { data: stats } = useQuery({
    queryKey: ['stats:today', projectId],
    queryFn: () => projectsApi.statsToday(projectId),
    enabled: !!projectId,
    staleTime: 30_000,
  })
  if (!stats?.in_progress) return null
  return (
    <div
      className="pointer-events-none absolute bottom-[18px] right-[22px] z-30 flex items-center gap-2 rounded-full border border-accent-500 bg-accent-500/[0.18] px-3.5 py-2 text-[12px] text-accent-300 backdrop-blur-md shadow-whisper"
      role="status"
      aria-live="polite"
    >
      <span aria-hidden className="status-dot in_progress" />
      <span className="font-medium">
        AI が {stats.in_progress} 件作業中
      </span>
      <Sparkle className="h-3 w-3 opacity-80" />
    </div>
  )
}
