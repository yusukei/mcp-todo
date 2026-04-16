import { useState, useCallback } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import TaskBoard from '../components/task/TaskBoard'
import TaskList from '../components/task/TaskList'
import TaskDetail from '../components/task/TaskDetail'
import TaskCreateModal from '../components/task/TaskCreateModal'
import ProjectDocumentsTab from '../components/project/ProjectDocumentsTab'
import ErrorTrackerView from './ErrorTrackerPage'
import { LayoutGrid, List, Plus, Archive, Filter, Columns3, FileText, Lock, CheckSquare, AlertTriangle } from 'lucide-react'
import { STATUS_OPTIONS, BOARD_COLUMNS } from '../constants/task'
import { showErrorToast } from '../components/common/Toast'
import type { Task, TaskStatus } from '../types'

type ViewMode = 'board' | 'list' | 'docs' | 'errors'

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialView = (searchParams.get('view') as ViewMode) || 'board'
  const [view, _setView] = useState<ViewMode>(['board', 'list', 'docs', 'errors'].includes(initialView) ? initialView as ViewMode : 'board')
  const setView = useCallback((v: ViewMode) => {
    _setView(v)
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (v === 'board') {
        next.delete('view')
      } else {
        next.set('view', v)
      }
      // Clear item selections from other tabs
      next.delete('task')
      next.delete('doc')
      return next
    }, { replace: true })
  }, [setSearchParams])
  const selectedTaskId = searchParams.get('task')
  const selectedDocId = searchParams.get('doc')
  const setSelectedItemId = useCallback((key: string, id: string | null) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (id) {
        next.set(key, id)
      } else {
        next.delete(key)
      }
      return next
    }, { replace: true })
  }, [setSearchParams])
  const setSelectedTaskId = useCallback((taskId: string | null) => setSelectedItemId('task', taskId), [setSelectedItemId])
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [selectMode, setSelectMode] = useState(false)
  const exitSelectMode = useCallback(() => setSelectMode(false), [])
  const [showColumnPicker, setShowColumnPicker] = useState(false)
  const [visibleColumns, setVisibleColumns] = useState<TaskStatus[]>(() => {
    const saved = localStorage.getItem(`board-columns:${projectId}`)
    if (saved) {
      try { return JSON.parse(saved) } catch (err) { console.warn('Corrupt board-columns in localStorage:', err) }
    }
    return BOARD_COLUMNS.map((c) => c.key)
  })
  const toggleColumn = (key: TaskStatus) => {
    setVisibleColumns((prev) => {
      const next = prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
      if (next.length === 0) return prev
      localStorage.setItem(`board-columns:${projectId}`, JSON.stringify(next))
      return next
    })
  }
  const qc = useQueryClient()

  const updateFlagsMutation = useMutation({
    mutationFn: ({ taskId, flags }: { taskId: string; flags: Record<string, boolean> }) =>
      api.patch(`/projects/${projectId}/tasks/${taskId}`, flags),
    onMutate: async ({ taskId, flags }) => {
      await qc.cancelQueries({ queryKey: ['tasks', projectId, showArchived] })
      const previousTasks = qc.getQueryData<Task[]>(['tasks', projectId, showArchived])
      qc.setQueryData<Task[]>(['tasks', projectId, showArchived], (old) =>
        old?.map((t) => (t.id === taskId ? { ...t, ...flags } : t))
      )
      return { previousTasks }
    },
    onError: (_err, _vars, context) => {
      if (context?.previousTasks) {
        qc.setQueryData(['tasks', projectId, showArchived], context.previousTasks)
      }
      showErrorToast('フラグの更新に失敗しました')
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
    },
  })

  const batchUpdateMutation = useMutation({
    mutationFn: (updates: { task_id: string; needs_detail?: boolean; approved?: boolean; archived?: boolean }[]) =>
      api.patch(`/projects/${projectId}/tasks/batch`, { updates }),
    onMutate: async (updates) => {
      await qc.cancelQueries({ queryKey: ['tasks', projectId, showArchived] })
      const previousTasks = qc.getQueryData<Task[]>(['tasks', projectId, showArchived])
      const updateMap = new Map(updates.map((u) => [u.task_id, u]))
      qc.setQueryData<Task[]>(['tasks', projectId, showArchived], (old) =>
        old?.map((t) => {
          const u = updateMap.get(t.id)
          return u ? { ...t, ...u } : t
        })
      )
      return { previousTasks }
    },
    onError: (_err, _vars, context) => {
      if (context?.previousTasks) {
        qc.setQueryData(['tasks', projectId, showArchived], context.previousTasks)
      }
      showErrorToast('一括更新に失敗しました')
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
    },
  })

  const archiveMutation = useMutation({
    mutationFn: ({ taskId, archive }: { taskId: string; archive: boolean }) =>
      api.post(`/projects/${projectId}/tasks/${taskId}/${archive ? 'archive' : 'unarchive'}`),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
    },
    onError: () => {
      showErrorToast('アーカイブの更新に失敗しました')
    },
  })

  const handleUpdateFlags = (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => {
    updateFlagsMutation.mutate({ taskId, flags })
  }

  const handleArchive = (taskId: string, archive: boolean) => {
    archiveMutation.mutate({ taskId, archive })
  }

  const handleBatchUpdateFlags = (taskIds: string[], flags: { needs_detail?: boolean; approved?: boolean }) => {
    batchUpdateMutation.mutate(taskIds.map((task_id) => ({ task_id, ...flags })))
  }

  const handleBatchArchive = (taskIds: string[]) => {
    batchUpdateMutation.mutate(taskIds.map((task_id) => ({ task_id, archived: true })))
  }

  const handleBatchUnarchive = (taskIds: string[]) => {
    batchUpdateMutation.mutate(taskIds.map((task_id) => ({ task_id, archived: false })))
  }

  const reorderMutation = useMutation({
    mutationFn: (taskIds: string[]) =>
      api.post(`/projects/${projectId}/tasks/reorder`, { task_ids: taskIds }),
    onMutate: async (taskIds) => {
      await qc.cancelQueries({ queryKey: ['tasks', projectId, showArchived] })
      const previousTasks = qc.getQueryData<Task[]>(['tasks', projectId, showArchived])
      qc.setQueryData<Task[]>(['tasks', projectId, showArchived], (old) => {
        if (!old) return old
        const orderMap = new Map(taskIds.map((id, i) => [id, i]))
        return old.map((t) => {
          const newOrder = orderMap.get(t.id)
          return newOrder !== undefined ? { ...t, sort_order: newOrder } : t
        })
      })
      return { previousTasks }
    },
    onError: (_err, _vars, context) => {
      if (context?.previousTasks) {
        qc.setQueryData(['tasks', projectId, showArchived], context.previousTasks)
      }
      showErrorToast('並び替えに失敗しました')
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
    },
  })

  const handleReorder = (taskIds: string[]) => {
    reorderMutation.mutate(taskIds)
  }

  const [exporting, setExporting] = useState(false)
  const handleExport = async (taskIds: string[], format: 'markdown' | 'pdf') => {
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
  }

  const statusChangeMutation = useMutation({
    mutationFn: ({ taskId, status }: { taskId: string; status: TaskStatus }) =>
      api.patch(`/projects/${projectId}/tasks/${taskId}`, { status }),
    onMutate: async ({ taskId, status }) => {
      await qc.cancelQueries({ queryKey: ['tasks', projectId, showArchived] })
      const previousTasks = qc.getQueryData<Task[]>(['tasks', projectId, showArchived])
      qc.setQueryData<Task[]>(['tasks', projectId, showArchived], (old) =>
        old?.map((t) => (t.id === taskId ? { ...t, status } : t))
      )
      return { previousTasks }
    },
    onError: (_err, _vars, context) => {
      if (context?.previousTasks) {
        qc.setQueryData(['tasks', projectId, showArchived], context.previousTasks)
      }
      showErrorToast('ステータスの更新に失敗しました')
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
    },
  })

  const handleStatusChange = (taskId: string, status: TaskStatus) => {
    statusChangeMutation.mutate({ taskId, status })
  }

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.get(`/projects/${projectId}`).then((r) => r.data),
    enabled: !!projectId,
  })

  // Only fetch statuses the UI actually displays
  const apiStatusFilter = (() => {
    if (showArchived) return undefined
    if (statusFilter !== 'all') return statusFilter
    return view === 'board' ? visibleColumns.join(',') : 'todo,in_progress,on_hold'
  })()

  const { data: tasks = [] } = useQuery({
    queryKey: ['tasks', projectId, showArchived, apiStatusFilter],
    queryFn: () => api.get(`/projects/${projectId}/tasks`, {
      params: {
        ...(showArchived ? {} : { archived: false }),
        ...(apiStatusFilter ? { status: apiStatusFilter } : {}),
      },
    }).then((r) => r.data.items),
    enabled: !!projectId,
  })

  // Exclude subtasks from kanban/list views (shown only in task detail panel)
  const topLevelTasks = tasks.filter((t: Task) => !t.parent_task_id)
  const filteredTasks = statusFilter === 'all' ? topLevelTasks : topLevelTasks.filter((t: Task) => t.status === statusFilter)

  if (!project) return <div className="p-8 text-gray-500 dark:text-gray-400" role="status" aria-live="polite">読み込み中...</div>

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="h-14 px-8 border-b border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: project.color }} />
          <h1 className="text-xl font-serif font-medium text-gray-800 dark:text-gray-100">{project.name}</h1>
          {project.is_locked && <Lock className="w-4 h-4 text-amber-500" />}
          {/* View switcher — anchored to left so position stays fixed */}
          <div className="flex items-center bg-gray-100 dark:bg-gray-700/60 rounded-lg p-0.5 ml-2">
            <button
              onClick={() => setView('board')}
              className={`p-1.5 rounded-md transition-colors ${view === 'board' ? 'bg-white dark:bg-gray-600 text-terracotta-600 dark:text-terracotta-400 shadow-sm' : 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'}`}
              title="カンバン"
            >
              <LayoutGrid className="w-4 h-4" />
            </button>
            <button
              onClick={() => setView('list')}
              className={`p-1.5 rounded-md transition-colors ${view === 'list' ? 'bg-white dark:bg-gray-600 text-terracotta-600 dark:text-terracotta-400 shadow-sm' : 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'}`}
              title="リスト"
            >
              <List className="w-4 h-4" />
            </button>
            <button
              onClick={() => setView('docs')}
              className={`p-1.5 rounded-md transition-colors ${view === 'docs' ? 'bg-white dark:bg-gray-600 text-terracotta-600 dark:text-terracotta-400 shadow-sm' : 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'}`}
              title="ドキュメント"
            >
              <FileText className="w-4 h-4" />
            </button>
            <button
              onClick={() => setView('errors')}
              className={`p-1.5 rounded-md transition-colors ${view === 'errors' ? 'bg-white dark:bg-gray-600 text-terracotta-600 dark:text-terracotta-400 shadow-sm' : 'text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300'}`}
              title="エラートラッカー"
            >
              <AlertTriangle className="w-4 h-4" />
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* Task controls — hidden in errors view */}
          {view !== 'errors' && (
            <>
              <div className="flex items-center gap-1">
                <Filter className="w-4 h-4 text-gray-400 dark:text-gray-500" />
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="text-sm border border-gray-200 dark:border-gray-600 rounded-lg px-2 py-1.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-focus"
                >
                  <option value="all">すべて</option>
                  {STATUS_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </div>
              {view === 'board' && (
                <div className="relative">
                  <button
                    onClick={() => setShowColumnPicker(!showColumnPicker)}
                    className={`p-2 rounded-lg transition-colors ${showColumnPicker ? 'bg-terracotta-100 dark:bg-terracotta-900/50 text-terracotta-600 dark:text-terracotta-400' : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
                    title="表示カラム"
                  >
                    <Columns3 className="w-5 h-5" />
                  </button>
                  {showColumnPicker && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setShowColumnPicker(false)} />
                      <div className="absolute right-0 top-full mt-1 bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg py-1 z-20 min-w-[140px]">
                        {BOARD_COLUMNS.map((col) => (
                          <label key={col.key} className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={visibleColumns.includes(col.key)}
                              onChange={() => toggleColumn(col.key)}
                              className="rounded border-gray-300 text-terracotta-600 focus:ring-focus w-3.5 h-3.5"
                            />
                            <span className="text-sm text-gray-700 dark:text-gray-200">{col.label}</span>
                          </label>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
              <button
                onClick={() => selectMode ? exitSelectMode() : setSelectMode(true)}
                className={`p-2 rounded-lg transition-colors ${selectMode ? 'bg-terracotta-100 dark:bg-terracotta-900/50 text-terracotta-600 dark:text-terracotta-400' : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
                title={selectMode ? '選択モード終了' : '選択モード'}
              >
                <CheckSquare className="w-5 h-5" />
              </button>
              <button
                onClick={() => setShowArchived(!showArchived)}
                className={`p-2 rounded-lg transition-colors ${showArchived ? 'bg-terracotta-100 dark:bg-terracotta-900/50 text-terracotta-600 dark:text-terracotta-400' : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
                title={showArchived ? 'アーカイブ済みを非表示' : 'アーカイブ済みを表示'}
              >
                <Archive className="w-5 h-5" />
              </button>

              {/* Primary action */}
              {!project.is_locked && (
                <>
                  <span className="w-px h-5 bg-gray-200 dark:bg-gray-600" />
                  <button
                    onClick={() => setShowCreateModal(true)}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-terracotta-500 text-gray-100 text-sm font-medium rounded-lg hover:bg-terracotta-600 transition-colors"
                  >
                    <Plus className="w-4 h-4" />
                    タスク追加
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {view === 'errors' ? (
          <ErrorTrackerView />
        ) : view === 'docs' ? (
          <div className="h-full overflow-y-auto">
            <ProjectDocumentsTab
              projectId={projectId!}
              initialDocumentId={selectedDocId ?? undefined}
              onSelectId={(id) => setSelectedItemId('doc', id)}
            />
          </div>
        ) : view === 'board' ? (
          <TaskBoard tasks={filteredTasks} projectId={projectId!} onTaskClick={setSelectedTaskId} onUpdateFlags={handleUpdateFlags} onArchive={handleArchive} onStatusChange={handleStatusChange} onExport={handleExport} onReorder={handleReorder} showArchived={showArchived} visibleColumns={visibleColumns} selectMode={selectMode} onExitSelectMode={exitSelectMode} />
        ) : (
          <TaskList tasks={filteredTasks} projectId={projectId!} selectMode={selectMode} onTaskClick={setSelectedTaskId} onUpdateFlags={handleUpdateFlags} onArchive={handleArchive} onBatchUpdateFlags={handleBatchUpdateFlags} onBatchArchive={handleBatchArchive} onBatchUnarchive={handleBatchUnarchive} onExport={handleExport} onReorder={handleReorder} showArchived={showArchived} />
        )}
      </div>

      {/* Task Detail Slide-over */}
      {selectedTaskId && (
        <TaskDetail
          taskId={selectedTaskId}
          projectId={projectId!}
          onClose={() => setSelectedTaskId(null)}
          onNavigateTask={setSelectedTaskId}
        />
      )}

      {showCreateModal && (
        <TaskCreateModal
          projectId={projectId!}
          onClose={() => setShowCreateModal(false)}
        />
      )}
    </div>
  )
}
