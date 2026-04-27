import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Project } from '../types'
import WorkbenchLayout from '../workbench/WorkbenchLayout'
import { getOrCreateClientId, subscribeCrossTab } from '../workbench/storage'
import { getServerLayout } from '../api/workbenchLayouts'
import { KNOWN_PANE_TYPES } from '../workbench/paneRegistry'
import {
  WorkbenchEventProvider,
  useWorkbenchEventBus,
} from '../workbench/eventBus'
import { getPreset } from '../workbench/presets'
import { showInfoToast, showSuccessToast } from '../components/common/Toast'
import TaskDetail from '../components/task/TaskDetail'
import type { PaneType } from '../workbench/types'
import { useWorkbenchStore } from '../workbench/useWorkbenchStore'
import { useInitialServerRefresh } from '../workbench/useInitialServerRefresh'
import { useWorkbenchHotkeys } from '../workbench/useWorkbenchHotkeys'
import { usePersistenceBeacon } from '../workbench/usePersistenceBeacon'

/**
 * Project Workbench page.
 *
 * Phase B 設計書 v2.1 (`69ed6f042835242574cad57c`) に従う action 駆動
 * 片方向フロー実装. v1 の useEffect 蛸足構造は `useWorkbenchStore` +
 * 専用 hook 群 (`useWorkbenchHotkeys`, `usePersistenceBeacon`,
 * `useInitialServerRefresh`) に分解した.
 *
 * **本コンポーネント内に残る useEffect は 1 個のみ** (SSE 受信 → dispatch
 * の bridge). cross-tab subscribe は `useWorkbenchStore` 内部で処理する.
 *
 * project 切替時は ``<WorkbenchPageBody key={projectId} />`` で remount
 * させる (lazy initializer の冪等性を維持).
 */
export default function WorkbenchPage() {
  const { projectId } = useParams<{ projectId: string }>()
  if (!projectId) {
    return <div className="p-8 text-gray-400">Invalid project id.</div>
  }
  return <WorkbenchPageBody key={projectId} projectId={projectId} />
}

interface BodyProps {
  projectId: string
}

function WorkbenchPageBody({ projectId }: BodyProps) {
  const { data: project, isLoading } = useQuery<Project>({
    queryKey: ['project', projectId],
    queryFn: () => api.get(`/projects/${projectId}`).then((r) => r.data),
    enabled: !!projectId,
  })

  const {
    state,
    dispatch,
    taskFallbackId,
    setTaskFallbackId,
    clearTaskFallback,
  } = useWorkbenchStore(projectId)

  // ── Effect 1: SSE 受信 (正当な外部同期: server → client) ─────
  //
  // 既存の useSSE が `workbench.layout.updated` 受信時に
  // ``['workbench-layout', projectId]`` を invalidate する. ここの
  // useQuery がその refetch を担い、結果が変わった時に
  // ``system.refreshFromServer`` action を dispatch する (I-7 ガード適用).
  //
  // 自タブの client_id を持つ payload は dispatch 前のフィルタ層で
  // 捨てる (Phase B 設計 v2.1 §5.2 / I-3: echo loop 構造防止).
  const { data: serverPayload } = useQuery({
    queryKey: ['workbench-layout', projectId],
    queryFn: () => getServerLayout(projectId),
    enabled: !!projectId,
    staleTime: Infinity,
    retry: false,
  })
  useEffect(() => {
    if (!serverPayload) return
    if (serverPayload.client_id === getOrCreateClientId()) return
    dispatch({
      kind: 'system.refreshFromServer',
      tree: serverPayload.tree,
      updatedAt: serverPayload.updated_at,
    })
  }, [serverPayload, dispatch])

  // ── reload-to-latest (Phase B v2.1 §4.4.6) ─────────────────
  // mount 直後の明示的 server fetch → system.refreshFromServer.
  // I-7 ガードでユーザの直近変更を保護する.
  useInitialServerRefresh(projectId, dispatch)

  // ── cross-tab subscribe (Effect 2: 正当な外部同期) ───────
  useEffect(() => {
    return subscribeCrossTab(projectId, KNOWN_PANE_TYPES, (tree, stamp) => {
      dispatch({ kind: 'remote.crossTab', tree, stamp })
    })
  }, [projectId, dispatch])

  // ── 永続化 beacon (visibilitychange / pagehide) ──────────
  usePersistenceBeacon(projectId, state)

  // ── reset 確認モーダル state ─────────────────────────────
  const [confirmReset, setConfirmReset] = useState<{ presetId: string } | null>(
    null,
  )

  // ── hotkey ────────────────────────────────────────────────
  useWorkbenchHotkeys({
    tree: state.tree,
    dispatch,
    onResetLayoutRequested: useCallback(
      () => setConfirmReset({ presetId: 'tasks-only' }),
      [],
    ),
  })

  // ── WorkbenchLayout に渡す callback 群 ───────────────────
  // dispatch を user.* action にラップした薄い adapter. 各 callback の
  // identity を安定させるため deps は [dispatch] のみ.
  const onActivateTab = useCallback(
    (groupId: string, tabId: string) =>
      dispatch({ kind: 'user.activateTab', groupId, tabId }),
    [dispatch],
  )
  const onCloseTab = useCallback(
    (groupId: string, tabId: string) =>
      dispatch({ kind: 'user.closeTab', groupId, tabId }),
    [dispatch],
  )
  const onAddTab = useCallback(
    (groupId: string, paneType: PaneType) =>
      dispatch({ kind: 'user.addTab', groupId, paneType }),
    [dispatch],
  )
  const onConfigChange = useCallback(
    (paneId: string, patch: Record<string, unknown>) =>
      dispatch({ kind: 'user.configChange', paneId, patch }),
    [dispatch],
  )
  const onSplit = useCallback(
    (groupId: string, orientation: 'horizontal' | 'vertical') =>
      dispatch({
        kind: 'user.split',
        groupId,
        orientation,
        newPaneType: 'tasks',
      }),
    [dispatch],
  )
  const onCloseGroup = useCallback(
    (groupId: string) => dispatch({ kind: 'user.closeGroup', groupId }),
    [dispatch],
  )
  const onSplitSizes = useCallback(
    (splitId: string, sizes: number[]) =>
      dispatch({ kind: 'user.splitSizes', splitId, sizes }),
    [dispatch],
  )
  const onMoveTab = useCallback(
    (
      paneId: string,
      targetGroupId: string,
      drop:
        | { kind: 'edge'; edge: 'top' | 'right' | 'bottom' | 'left' }
        | { kind: 'center'; index: number },
    ) =>
      dispatch({
        kind: 'user.moveTab',
        paneId,
        targetGroupId,
        drop,
      }),
    [dispatch],
  )
  const onCopyUrl = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(window.location.href)
      showSuccessToast('URL をクリップボードにコピーしました')
    } catch {
      showInfoToast('クリップボードへのアクセスに失敗しました')
    }
  }, [])
  const onResetLayout = useCallback(
    () => setConfirmReset({ presetId: 'tasks-only' }),
    [],
  )
  const onLoadPreset = useCallback(
    (presetId: string) => setConfirmReset({ presetId }),
    [],
  )

  const projectName = useMemo(
    () => project?.name ?? projectId ?? '...',
    [project, projectId],
  )

  if (isLoading && !project) {
    return <div className="p-8 text-gray-400">Loading project…</div>
  }

  return (
    <div className="paper-grain relative flex flex-col h-full overflow-hidden bg-gray-900">
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
            clearTaskFallback={clearTaskFallback}
          />
        </WorkbenchEventProvider>
      </div>

      {confirmReset && (
        <ResetConfirmModal
          presetLabel={
            getPreset(confirmReset.presetId)?.label ?? 'the selected preset'
          }
          onCancel={() => setConfirmReset(null)}
          onConfirm={() => {
            dispatch({
              kind: 'user.applyPreset',
              presetId: confirmReset.presetId,
            })
            setConfirmReset(null)
          }}
        />
      )}
    </div>
  )
}

// ── Fallback slide-overs (Decision D1) ────────────────────────

interface WorkbenchFallbacksProps {
  projectId: string
  taskFallbackId: string | null
  setTaskFallbackId: (id: string | null) => void
  clearTaskFallback: () => void
}

function WorkbenchFallbacks({
  projectId,
  taskFallbackId,
  setTaskFallbackId,
  clearTaskFallback,
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
      onClose={clearTaskFallback}
      onNavigateTask={(next) => setTaskFallbackId(next)}
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
          discard your current Workbench arrangement (tab positions, splits,
          per-pane configs). Per-pane data on the server (tasks, documents,
          terminal sessions) is unaffected.
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
