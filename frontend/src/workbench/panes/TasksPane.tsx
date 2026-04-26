/**
 * Tasks pane — Phase C2 D1-b/4 expansion.
 *
 * Renders the project's tasks in Board / List / Timeline view, with
 * the same mutation set as the legacy ProjectPage (filter / select /
 * archive / batch actions / column picker / reorder / status change /
 * export). Click on a task emits ``open-task`` to the workbench bus
 * (routed to a TaskDetailPane or slide-over fallback by the bus).
 *
 * Decision D5 (Phase C2 v2.4): when the pane is narrower than
 * ``LIST_FORCE_BREAKPOINT`` px the user-selected board/timeline view
 * is *visually* swapped for the list — Board with 5 columns squeezed
 * into < 640 px is unusable. The user's persisted ``viewMode`` is
 * preserved so widening the pane restores their original choice.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { lazy, Suspense } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Columns3,
  Filter,
  LayoutGrid,
  List,
  Loader2,
  GanttChartSquare,
  Plus,
} from 'lucide-react'
import { api } from '../../api/client'
import TaskBoard from '../../components/task/TaskBoard'
import TaskList from '../../components/task/TaskList'
import TaskCreateModal from '../../components/task/TaskCreateModal'
import { showErrorToast } from '../../components/common/Toast'
import { BOARD_COLUMNS, STATUS_OPTIONS } from '../../constants/task'
import type { Task, TaskStatus } from '../../types'
import type { PaneComponentProps } from '../paneRegistry'
import { useWorkbenchEventBus } from '../eventBus'

const TaskTimeline = lazy(() => import('../../components/task/TaskTimeline'))

const VIEW_MODES = ['board', 'list', 'timeline'] as const
type ViewMode = (typeof VIEW_MODES)[number]

const isViewMode = (v: unknown): v is ViewMode =>
  typeof v === 'string' && (VIEW_MODES as readonly string[]).includes(v)

const lastViewKey = (projectId: string) => `lastView:${projectId}`
const boardColumnsKey = (projectId: string) => `board-columns:${projectId}`

/**
 * Threshold below which ``board`` and ``timeline`` views auto-degrade
 * to ``list``. 640 px (v2.4 暫定) — see Plan v2.4 §5.7. The actual
 * minimum-usable Board column width is 200-240 px × 5 columns ≈
 * 1000-1200 px; below that the user gets a horizontal scroll fight.
 * 640 px catches the common 13" laptop 2-pane split (each pane =
 * 640 px) and forces list before Board becomes painful.
 */
const LIST_FORCE_BREAKPOINT = 640

interface PaneConfig {
  viewMode?: ViewMode
}

export default function TasksPane({
  paneId,
  projectId,
  paneConfig,
  onConfigChange,
}: PaneComponentProps) {
  void paneId
  const bus = useWorkbenchEventBus()
  const qc = useQueryClient()

  // ── Persisted per-pane state ─────────────────────────────────

  const persistedView = (paneConfig as PaneConfig).viewMode
  const userView: ViewMode = isViewMode(persistedView) ? persistedView : 'board'

  // Seed paneConfig.viewMode from the legacy `lastView:<projectId>`
  // key on first mount so the user's existing preference flows
  // through. After this the pane owns the viewMode and writes to
  // paneConfig (other tabs / standalone ProjectPage continue to use
  // the legacy key).
  useEffect(() => {
    if (persistedView) return
    try {
      const legacy = window.localStorage.getItem(lastViewKey(projectId))
      if (isViewMode(legacy)) {
        onConfigChange({ viewMode: legacy })
      }
    } catch {
      /* ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const setViewMode = useCallback(
    (m: ViewMode) => {
      onConfigChange({ viewMode: m })
      try {
        // Mirror to legacy key so the standalone ProjectPage stays in
        // sync until D3 deletes that page entirely.
        window.localStorage.setItem(lastViewKey(projectId), m)
      } catch {
        /* ignore */
      }
    },
    [onConfigChange, projectId],
  )

  // ── Pane width tracking (Decision D5: < 640 px = list view) ──

  const containerRef = useRef<HTMLDivElement>(null)
  const [paneWidth, setPaneWidth] = useState<number>(Infinity)
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setPaneWidth(entry.contentRect.width)
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const isNarrow = paneWidth < LIST_FORCE_BREAKPOINT
  // Effective view: if the pane is too narrow, fall back to list
  // even though paneConfig still records the user's choice. Widening
  // the pane restores the original.
  const effectiveView: ViewMode = isNarrow && userView !== 'list' ? 'list' : userView

  // ── Filters / select mode (component-local state) ────────────

  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [showArchived, setShowArchived] = useState(false)
  const [selectMode, setSelectMode] = useState(false)
  const [showColumnPicker, setShowColumnPicker] = useState(false)
  // TP1: Create-task affordance state. Modal is the same component
  // legacy ProjectPage used; on close it just unmounts (the mutation
  // inside handles its own server invalidation).
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [visibleColumns, setVisibleColumns] = useState<TaskStatus[]>(() => {
    try {
      const saved = window.localStorage.getItem(boardColumnsKey(projectId))
      if (saved) return JSON.parse(saved)
    } catch {
      /* ignore */
    }
    return BOARD_COLUMNS.map((c) => c.key)
  })
  const toggleColumn = useCallback(
    (key: TaskStatus) => {
      setVisibleColumns((prev) => {
        const next = prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
        if (next.length === 0) return prev
        try {
          window.localStorage.setItem(boardColumnsKey(projectId), JSON.stringify(next))
        } catch {
          /* ignore */
        }
        return next
      })
    },
    [projectId],
  )
  const exitSelectMode = useCallback(() => setSelectMode(false), [])

  // ── Task fetch (mirrors ProjectPage's filter logic) ──────────

  const apiStatusFilter = useMemo(() => {
    if (showArchived) return undefined
    if (statusFilter !== 'all') return statusFilter
    if (effectiveView === 'board') return visibleColumns.join(',')
    if (effectiveView === 'timeline') return undefined
    return 'todo,in_progress,on_hold'
  }, [showArchived, statusFilter, effectiveView, visibleColumns])

  const tasksQueryKey = useMemo(
    () => ['tasks', projectId, showArchived, apiStatusFilter] as const,
    [projectId, showArchived, apiStatusFilter],
  )

  const { data: tasks = [], isLoading, isError } = useQuery<Task[]>({
    queryKey: tasksQueryKey,
    queryFn: () =>
      api
        .get(`/projects/${projectId}/tasks`, {
          params: {
            ...(showArchived ? {} : { archived: false }),
            ...(apiStatusFilter ? { status: apiStatusFilter } : {}),
          },
        })
        .then((r) => (r.data?.items ?? []) as Task[]),
  })

  // TP3: project query so the create-task affordance can be hidden
  // when the project is locked. This is a separate query from the
  // task list — sharing the same key as WorkbenchPage's project
  // fetch lets React Query dedupe the request.
  const { data: projectMeta } = useQuery<{ is_locked?: boolean }>({
    queryKey: ['project', projectId],
    queryFn: () => api.get(`/projects/${projectId}`).then((r) => r.data),
    enabled: !!projectId,
  })

  // Subtasks render only inside TaskDetail; exclude from Board/List/Timeline.
  const topLevelTasks = useMemo(
    () => tasks.filter((t) => !t.parent_task_id),
    [tasks],
  )
  const filteredTasks = useMemo(
    () =>
      statusFilter === 'all'
        ? topLevelTasks
        : topLevelTasks.filter((t) => t.status === statusFilter),
    [topLevelTasks, statusFilter],
  )

  // ── Mutations (verbatim from ProjectPage, scoped to this pane) ─

  const updateFlagsMutation = useMutation({
    mutationFn: ({ taskId, flags }: { taskId: string; flags: Record<string, boolean> }) =>
      api.patch(`/projects/${projectId}/tasks/${taskId}`, flags),
    onMutate: async ({ taskId, flags }) => {
      await qc.cancelQueries({ queryKey: ['tasks', projectId] })
      const previous = qc.getQueryData<Task[]>(tasksQueryKey)
      qc.setQueryData<Task[]>(tasksQueryKey, (old) =>
        old?.map((t) => (t.id === taskId ? { ...t, ...flags } : t)),
      )
      return { previous }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(tasksQueryKey, ctx.previous)
      showErrorToast('フラグの更新に失敗しました')
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['tasks', projectId] }),
  })

  const archiveMutation = useMutation({
    mutationFn: ({ taskId, archive }: { taskId: string; archive: boolean }) =>
      api.post(`/projects/${projectId}/tasks/${taskId}/${archive ? 'archive' : 'unarchive'}`),
    onSettled: () => qc.invalidateQueries({ queryKey: ['tasks', projectId] }),
    onError: () => showErrorToast('アーカイブの更新に失敗しました'),
  })

  const batchUpdateMutation = useMutation({
    mutationFn: (
      updates: { task_id: string; needs_detail?: boolean; approved?: boolean; archived?: boolean }[],
    ) => api.patch(`/projects/${projectId}/tasks/batch`, { updates }),
    onMutate: async (updates) => {
      await qc.cancelQueries({ queryKey: ['tasks', projectId] })
      const previous = qc.getQueryData<Task[]>(tasksQueryKey)
      const map = new Map(updates.map((u) => [u.task_id, u]))
      qc.setQueryData<Task[]>(tasksQueryKey, (old) =>
        old?.map((t) => (map.has(t.id) ? { ...t, ...map.get(t.id)! } : t)),
      )
      return { previous }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(tasksQueryKey, ctx.previous)
      showErrorToast('一括更新に失敗しました')
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['tasks', projectId] }),
  })

  const reorderMutation = useMutation({
    mutationFn: (taskIds: string[]) =>
      api.post(`/projects/${projectId}/tasks/reorder`, { task_ids: taskIds }),
    onMutate: async (taskIds) => {
      await qc.cancelQueries({ queryKey: ['tasks', projectId] })
      const previous = qc.getQueryData<Task[]>(tasksQueryKey)
      qc.setQueryData<Task[]>(tasksQueryKey, (old) => {
        if (!old) return old
        const orderMap = new Map(taskIds.map((id, i) => [id, i]))
        return old.map((t) => {
          const o = orderMap.get(t.id)
          return o !== undefined ? { ...t, sort_order: o } : t
        })
      })
      return { previous }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(tasksQueryKey, ctx.previous)
      showErrorToast('並び替えに失敗しました')
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['tasks', projectId] }),
  })

  const statusChangeMutation = useMutation({
    mutationFn: ({ taskId, status }: { taskId: string; status: TaskStatus }) =>
      api.patch(`/projects/${projectId}/tasks/${taskId}`, { status }),
    onMutate: async ({ taskId, status }) => {
      await qc.cancelQueries({ queryKey: ['tasks', projectId] })
      const previous = qc.getQueryData<Task[]>(tasksQueryKey)
      qc.setQueryData<Task[]>(tasksQueryKey, (old) =>
        old?.map((t) => (t.id === taskId ? { ...t, status } : t)),
      )
      return { previous }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(tasksQueryKey, ctx.previous)
      showErrorToast('ステータスの更新に失敗しました')
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['tasks', projectId] }),
  })

  // ── Handler bindings ─────────────────────────────────────────

  const handleTaskClick = useCallback(
    (taskId: string) => bus.emit('open-task', { taskId }),
    [bus],
  )
  const handleUpdateFlags = useCallback(
    (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) =>
      updateFlagsMutation.mutate({ taskId, flags }),
    [updateFlagsMutation],
  )
  const handleArchive = useCallback(
    (taskId: string, archive: boolean) =>
      archiveMutation.mutate({ taskId, archive }),
    [archiveMutation],
  )
  const handleBatchUpdateFlags = useCallback(
    (taskIds: string[], flags: { needs_detail?: boolean; approved?: boolean }) =>
      batchUpdateMutation.mutate(taskIds.map((task_id) => ({ task_id, ...flags }))),
    [batchUpdateMutation],
  )
  const handleBatchArchive = useCallback(
    (taskIds: string[]) =>
      batchUpdateMutation.mutate(taskIds.map((task_id) => ({ task_id, archived: true }))),
    [batchUpdateMutation],
  )
  const handleBatchUnarchive = useCallback(
    (taskIds: string[]) =>
      batchUpdateMutation.mutate(taskIds.map((task_id) => ({ task_id, archived: false }))),
    [batchUpdateMutation],
  )
  const handleReorder = useCallback(
    (taskIds: string[]) => reorderMutation.mutate(taskIds),
    [reorderMutation],
  )
  const handleStatusChange = useCallback(
    (taskId: string, status: TaskStatus) =>
      statusChangeMutation.mutate({ taskId, status }),
    [statusChangeMutation],
  )

  const [exporting, setExporting] = useState(false)
  const handleExport = useCallback(
    async (taskIds: string[], format: 'markdown' | 'pdf') => {
      if (exporting) return
      setExporting(true)
      try {
        const resp = await api.post(
          `/projects/${projectId}/tasks/export`,
          { task_ids: taskIds, format },
          { responseType: 'blob', timeout: 120000 },
        )
        const blob = new Blob([resp.data])
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = format === 'markdown' ? 'tasks.md' : 'tasks.pdf'
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(url)
      } catch {
        showErrorToast('エクスポートに失敗しました')
      } finally {
        setExporting(false)
      }
    },
    [exporting, projectId],
  )

  // ── Render ───────────────────────────────────────────────────

  return (
    <div ref={containerRef} className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 text-xs flex-wrap">
        <ViewModeSwitch
          mode={userView}
          effective={effectiveView}
          onChange={setViewMode}
          isNarrow={isNarrow}
        />
        <div className="flex items-center gap-1 ml-2">
          <Filter className="w-3 h-3 text-gray-400" />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-xs bg-transparent text-gray-600 dark:text-gray-300 focus:outline-none"
          >
            <option value="all">All</option>
            {STATUS_OPTIONS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => setShowArchived((v) => !v)}
          className={`flex items-center gap-1 px-1.5 py-0.5 rounded ${
            showArchived
              ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200'
              : 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'
          }`}
          title="アーカイブ済を表示"
        >
          <Archive className="w-3 h-3" />
        </button>
        <button
          type="button"
          onClick={() => setSelectMode((v) => !v)}
          className={`px-1.5 py-0.5 rounded ${
            selectMode
              ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200'
              : 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'
          }`}
          title="複数選択モード"
        >
          Sel
        </button>
        {effectiveView === 'board' && !isNarrow && (
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowColumnPicker((v) => !v)}
              className="flex items-center gap-1 px-1.5 py-0.5 text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
              title="表示する列を選ぶ"
            >
              <Columns3 className="w-3 h-3" />
            </button>
            {showColumnPicker && (
              <div
                className="absolute right-0 top-full z-20 mt-1 w-44 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg p-1"
                onMouseLeave={() => setShowColumnPicker(false)}
              >
                {BOARD_COLUMNS.map((c) => (
                  <label
                    key={c.key}
                    className="flex items-center gap-2 px-2 py-1 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 rounded cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={visibleColumns.includes(c.key)}
                      onChange={() => toggleColumn(c.key)}
                    />
                    {c.label}
                  </label>
                ))}
              </div>
            )}
          </div>
        )}
        {selectMode && (
          <button
            type="button"
            onClick={exitSelectMode}
            className="text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
          >
            選択モード終了
          </button>
        )}
        {/* TP1: Create task affordance. Anchored to the right edge so
            it stays visible regardless of how many filter / select /
            column-picker buttons populate the toolbar. Hidden when the
            project is locked (TP3). */}
        {!projectMeta?.is_locked && (
          <button
            type="button"
            onClick={() => setShowCreateModal(true)}
            className="ml-auto flex items-center gap-1 px-2 py-0.5 rounded bg-accent-500 text-white hover:bg-accent-600 text-xs"
            title="タスク追加"
            aria-label="タスク追加"
          >
            <Plus className="w-3 h-3" />
            タスク追加
          </button>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {isLoading ? (
          <div className="h-full flex items-center justify-center text-gray-500">
            <Loader2 className="w-5 h-5 animate-spin" />
          </div>
        ) : isError ? (
          <div className="h-full flex items-center justify-center text-sm text-red-500">
            Failed to load tasks for this project.
          </div>
        ) : effectiveView === 'board' ? (
          <TaskBoard
            tasks={filteredTasks}
            projectId={projectId}
            onTaskClick={handleTaskClick}
            onUpdateFlags={handleUpdateFlags}
            onArchive={handleArchive}
            onStatusChange={handleStatusChange}
            onExport={handleExport}
            onReorder={handleReorder}
            showArchived={showArchived}
            visibleColumns={visibleColumns}
            selectMode={selectMode}
            onExitSelectMode={exitSelectMode}
          />
        ) : effectiveView === 'timeline' ? (
          <Suspense
            fallback={
              <div className="h-full flex items-center justify-center text-gray-400">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            }
          >
            <TaskTimeline
              tasks={filteredTasks}
              projectId={projectId}
              onTaskClick={handleTaskClick}
            />
          </Suspense>
        ) : (
          <TaskList
            tasks={filteredTasks}
            projectId={projectId}
            selectMode={selectMode}
            onTaskClick={handleTaskClick}
            onUpdateFlags={handleUpdateFlags}
            onArchive={handleArchive}
            onBatchUpdateFlags={handleBatchUpdateFlags}
            onBatchArchive={handleBatchArchive}
            onBatchUnarchive={handleBatchUnarchive}
            onExport={handleExport}
            onReorder={handleReorder}
            showArchived={showArchived}
          />
        )}
      </div>

      {showCreateModal && (
        <TaskCreateModal
          projectId={projectId}
          onClose={() => setShowCreateModal(false)}
        />
      )}
    </div>
  )
}

interface ViewModeSwitchProps {
  mode: ViewMode
  effective: ViewMode
  onChange: (m: ViewMode) => void
  isNarrow: boolean
}

function ViewModeSwitch({ mode, effective, onChange, isNarrow }: ViewModeSwitchProps) {
  const ICON: Record<ViewMode, React.FC<{ className?: string }>> = {
    board: LayoutGrid,
    list: List,
    timeline: GanttChartSquare,
  }
  return (
    <div className="flex items-center gap-0.5">
      {VIEW_MODES.map((m) => {
        const Icon = ICON[m]
        const isActive = m === mode
        const isDegraded = isNarrow && m !== 'list' && isActive
        return (
          <button
            key={m}
            type="button"
            onClick={() => onChange(m)}
            className={`p-1 rounded ${
              isActive
                ? isDegraded
                  ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                  : 'bg-gray-200 text-gray-900 dark:bg-gray-700 dark:text-gray-100'
                : 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'
            }`}
            title={
              isDegraded
                ? `${m} は狭い pane では list 表示になります (pane を広げると ${m} に戻る)`
                : m === 'board'
                  ? 'カンバン'
                  : m === 'list'
                    ? 'リスト'
                    : 'タイムライン (Gantt)'
            }
          >
            <Icon className="w-3.5 h-3.5" />
          </button>
        )
      })}
      {isNarrow && effective === 'list' && mode !== 'list' && (
        <span className="text-[10px] text-amber-600 dark:text-amber-400 ml-1">
          狭幅 list 強制
        </span>
      )}
    </div>
  )
}
