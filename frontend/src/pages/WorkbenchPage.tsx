import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
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
  setActiveTab,
  setSplitSizes,
  splitTabGroup,
  updatePaneConfig,
  validateTree,
} from '../workbench/treeUtils'
import type { LayoutTree, PaneType } from '../workbench/types'
import { KNOWN_PANE_TYPES } from '../workbench/paneRegistry'
import { WorkbenchEventProvider } from '../workbench/eventBus'

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
        <Link
          to={`/projects/${projectId}`}
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          {projectName}
        </Link>
        <span className="text-gray-400">/</span>
        <span className="text-sm font-medium text-gray-800 dark:text-gray-200">
          Workbench
        </span>
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
          />
        </WorkbenchEventProvider>
      </div>
    </div>
  )
}
