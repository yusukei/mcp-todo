import { useState, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CornerDownRight, Plus, X } from 'lucide-react'
import clsx from 'clsx'
import { api } from '../../api/client'
import type { Task } from '../../types'
import { STATUS_LABELS, STATUS_COLORS, PRIORITY_DOT_COLORS } from '../../constants/task'
import { showErrorToast, showSuccessToast } from '../common/Toast'

interface Props {
  task: Task
  projectId: string
  onTaskClick?: (taskId: string) => void
}

export default function TaskSubtaskSection({ task, projectId, onTaskClick }: Props) {
  const qc = useQueryClient()
  const [showSubtaskForm, setShowSubtaskForm] = useState(false)
  const [subtaskTitle, setSubtaskTitle] = useState('')
  const subtaskInputRef = useRef<HTMLInputElement>(null)

  const { data: subtasks = [] } = useQuery<Task[]>({
    queryKey: ['subtasks', projectId, task.id],
    queryFn: () => api.get(`/projects/${projectId}/tasks`, { params: { parent_task_id: task.id } }).then((r) => r.data.items),
    enabled: !!projectId && !!task.id,
  })

  const createSubtask = useMutation({
    mutationFn: (title: string) =>
      api.post(`/projects/${projectId}/tasks`, {
        title,
        parent_task_id: task.id,
        priority: 'medium',
        status: 'todo',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['subtasks', projectId, task.id] })
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
      setSubtaskTitle('')
      setShowSubtaskForm(false)
      showSuccessToast('サブタスクを作成しました')
    },
    onError: () => {
      showErrorToast('サブタスクの作成に失敗しました')
    },
  })

  const handleCreateSubtask = () => {
    if (!subtaskTitle.trim()) return
    createSubtask.mutate(subtaskTitle.trim())
  }

  const inputClasses = 'w-full border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus'

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="block text-sm font-medium text-gray-600 dark:text-gray-400">
          サブタスク ({subtasks.length})
        </label>
        {!showSubtaskForm && (
          <button
            onClick={() => {
              setShowSubtaskForm(true)
              setTimeout(() => subtaskInputRef.current?.focus(), 0)
            }}
            className="flex items-center gap-1 text-xs text-accent-600 dark:text-accent-400 hover:text-accent-800 dark:hover:text-accent-300 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            サブタスク追加
          </button>
        )}
      </div>
      {showSubtaskForm && (
        <div className="flex gap-2 mb-3">
          <input
            ref={subtaskInputRef}
            type="text"
            value={subtaskTitle}
            onChange={(e) => setSubtaskTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreateSubtask()
              if (e.key === 'Escape') {
                setShowSubtaskForm(false)
                setSubtaskTitle('')
              }
            }}
            className={inputClasses}
            placeholder="サブタスクのタイトル"
          />
          <button
            onClick={handleCreateSubtask}
            disabled={!subtaskTitle.trim() || createSubtask.isPending}
            className="px-3 py-2 text-sm text-white bg-accent-600 rounded-lg hover:bg-accent-600 disabled:opacity-40 flex-shrink-0"
          >
            追加
          </button>
          <button
            onClick={() => {
              setShowSubtaskForm(false)
              setSubtaskTitle('')
            }}
            className="px-2 py-2 text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 flex-shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}
      {subtasks.length > 0 ? (
        <div className="space-y-1">
          {subtasks.map((st) => (
            <div
              key={st.id}
              onClick={() => onTaskClick?.(st.id)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer transition-colors"
            >
              <CornerDownRight className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500 flex-shrink-0" />
              <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', PRIORITY_DOT_COLORS[st.priority])} />
              <span className="flex-1 text-sm text-gray-800 dark:text-gray-100 truncate">{st.title}</span>
              <span className={clsx('text-xs px-2 py-0.5 rounded-full flex-shrink-0', STATUS_COLORS[st.status])}>
                {STATUS_LABELS[st.status]}
              </span>
            </div>
          ))}
        </div>
      ) : !showSubtaskForm ? (
        <p className="text-sm text-gray-400 dark:text-gray-500">サブタスクはありません</p>
      ) : null}
    </div>
  )
}
