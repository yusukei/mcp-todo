import { useState, useCallback } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import TaskBoard from '../components/task/TaskBoard'
import TaskList from '../components/task/TaskList'
import TaskDetail from '../components/task/TaskDetail'
import TaskCreateModal from '../components/task/TaskCreateModal'
import { LayoutGrid, List, Plus, Archive, Filter, Columns3 } from 'lucide-react'
import { STATUS_OPTIONS, BOARD_COLUMNS } from '../constants/task'
import { showErrorToast } from '../components/common/Toast'
import type { Task, TaskStatus } from '../types'

type ViewMode = 'board' | 'list'

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const [view, setView] = useState<ViewMode>('board')
  const selectedTaskId = searchParams.get('task')
  const setSelectedTaskId = useCallback((taskId: string | null) => {
    setSearchParams(taskId ? { task: taskId } : {}, { replace: true })
  }, [setSearchParams])
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [showColumnPicker, setShowColumnPicker] = useState(false)
  const [visibleColumns, setVisibleColumns] = useState<TaskStatus[]>(() => {
    const saved = localStorage.getItem(`board-columns:${projectId}`)
    if (saved) {
      try { return JSON.parse(saved) } catch { /* ignore */ }
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

  const { data: tasks = [] } = useQuery({
    queryKey: ['tasks', projectId, showArchived],
    queryFn: () => api.get(`/projects/${projectId}/tasks`, {
      params: { ...(showArchived ? {} : { archived: false }), limit: 200 },
    }).then((r) => r.data.items),
    enabled: !!projectId,
  })

  const filteredTasks = statusFilter === 'all' ? tasks : tasks.filter((t: Task) => t.status === statusFilter)

  if (!project) return <div className="p-8 text-gray-500 dark:text-gray-400" role="status" aria-live="polite">読み込み中...</div>

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-8 py-4 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: project.color }} />
          <h1 className="text-xl font-bold text-gray-800 dark:text-gray-100">{project.name}</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors"
          >
            <Plus className="w-4 h-4" />
            タスク追加
          </button>
          <div className="flex items-center gap-1">
            <Filter className="w-4 h-4 text-gray-400 dark:text-gray-500" />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="text-sm border border-gray-200 dark:border-gray-600 rounded-lg px-2 py-1.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
                className={`p-2 rounded-lg transition-colors ${showColumnPicker ? 'bg-indigo-100 dark:bg-indigo-900/50 text-indigo-600 dark:text-indigo-400' : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
                title="表示カラム"
              >
                <Columns3 className="w-5 h-5" />
              </button>
              {showColumnPicker && (
                <div className="absolute right-0 top-full mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg py-1 z-20 min-w-[140px]">
                  {BOARD_COLUMNS.map((col) => (
                    <label key={col.key} className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={visibleColumns.includes(col.key)}
                        onChange={() => toggleColumn(col.key)}
                        className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5"
                      />
                      <span className="text-sm text-gray-700 dark:text-gray-200">{col.label}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
          <button
            onClick={() => setShowArchived(!showArchived)}
            className={`p-2 rounded-lg transition-colors ${showArchived ? 'bg-indigo-100 dark:bg-indigo-900/50 text-indigo-600 dark:text-indigo-400' : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
            title={showArchived ? 'アーカイブ済みを非表示' : 'アーカイブ済みを表示'}
          >
            <Archive className="w-5 h-5" />
          </button>
          <button
            onClick={() => setView('board')}
            className={`p-2 rounded-lg transition-colors ${view === 'board' ? 'bg-indigo-100 dark:bg-indigo-900/50 text-indigo-600 dark:text-indigo-400' : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
            title="カンバン"
          >
            <LayoutGrid className="w-5 h-5" />
          </button>
          <button
            onClick={() => setView('list')}
            className={`p-2 rounded-lg transition-colors ${view === 'list' ? 'bg-indigo-100 dark:bg-indigo-900/50 text-indigo-600 dark:text-indigo-400' : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
            title="リスト"
          >
            <List className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {view === 'board' ? (
          <TaskBoard tasks={filteredTasks} projectId={projectId!} onTaskClick={setSelectedTaskId} onUpdateFlags={handleUpdateFlags} onArchive={handleArchive} onStatusChange={handleStatusChange} showArchived={showArchived} visibleColumns={visibleColumns} />
        ) : (
          <TaskList tasks={filteredTasks} projectId={projectId!} onTaskClick={setSelectedTaskId} onUpdateFlags={handleUpdateFlags} onArchive={handleArchive} showArchived={showArchived} />
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
