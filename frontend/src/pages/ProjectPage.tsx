import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import TaskBoard from '../components/task/TaskBoard'
import TaskList from '../components/task/TaskList'
import TaskDetail from '../components/task/TaskDetail'
import TaskCreateModal from '../components/task/TaskCreateModal'
import { LayoutGrid, List, Plus } from 'lucide-react'

type ViewMode = 'board' | 'list'

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [view, setView] = useState<ViewMode>('board')
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [showCreateModal, setShowCreateModal] = useState(false)

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.get(`/projects/${projectId}`).then((r) => r.data),
    enabled: !!projectId,
  })

  const { data: tasks = [] } = useQuery({
    queryKey: ['tasks', projectId],
    queryFn: () => api.get(`/projects/${projectId}/tasks`).then((r) => r.data),
    enabled: !!projectId,
  })

  if (!project) return <div className="p-8 text-gray-500">読み込み中...</div>

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-8 py-4 border-b border-gray-200 bg-white flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: project.color }} />
          <h1 className="text-xl font-bold text-gray-800">{project.name}</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors"
          >
            <Plus className="w-4 h-4" />
            タスク追加
          </button>
          <button
            onClick={() => setView('board')}
            className={`p-2 rounded-lg transition-colors ${view === 'board' ? 'bg-indigo-100 text-indigo-600' : 'text-gray-400 hover:bg-gray-100'}`}
            title="カンバン"
          >
            <LayoutGrid className="w-5 h-5" />
          </button>
          <button
            onClick={() => setView('list')}
            className={`p-2 rounded-lg transition-colors ${view === 'list' ? 'bg-indigo-100 text-indigo-600' : 'text-gray-400 hover:bg-gray-100'}`}
            title="リスト"
          >
            <List className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {view === 'board' ? (
          <TaskBoard tasks={tasks} projectId={projectId!} onTaskClick={setSelectedTaskId} />
        ) : (
          <TaskList tasks={tasks} projectId={projectId!} onTaskClick={setSelectedTaskId} />
        )}
      </div>

      {/* Task Detail Slide-over */}
      {selectedTaskId && (
        <TaskDetail
          taskId={selectedTaskId}
          projectId={projectId!}
          onClose={() => setSelectedTaskId(null)}
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
