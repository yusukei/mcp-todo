import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { X, Send } from 'lucide-react'
import { api } from '../../api/client'
import clsx from 'clsx'

const STATUS_OPTIONS = [
  { value: 'todo', label: 'TODO' },
  { value: 'in_progress', label: '進行中' },
  { value: 'in_review', label: 'レビュー中' },
  { value: 'done', label: '完了' },
  { value: 'cancelled', label: 'キャンセル' },
]

interface Props {
  taskId: string
  projectId: string
  onClose: () => void
}

export default function TaskDetail({ taskId, projectId, onClose }: Props) {
  const qc = useQueryClient()
  const [comment, setComment] = useState('')

  const { data: task } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.get(`/projects/${projectId}/tasks/${taskId}`).then((r) => r.data),
  })

  const updateStatus = useMutation({
    mutationFn: (status: string) =>
      api.patch(`/projects/${projectId}/tasks/${taskId}`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
      qc.invalidateQueries({ queryKey: ['task', taskId] })
    },
  })

  const addComment = useMutation({
    mutationFn: (content: string) =>
      api.post(`/projects/${projectId}/tasks/${taskId}/comments`, { content }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      setComment('')
    },
  })

  if (!task) return null

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/30" onClick={onClose} />
      <div className="w-full max-w-lg bg-white shadow-xl flex flex-col h-full overflow-hidden">
        {/* Header */}
        <div className="flex items-start justify-between p-6 border-b">
          <h2 className="text-lg font-semibold text-gray-800 flex-1 pr-4">{task.title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Status */}
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-2">ステータス</label>
            <div className="flex flex-wrap gap-2">
              {STATUS_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => updateStatus.mutate(opt.value)}
                  className={clsx(
                    'px-3 py-1 text-sm rounded-full border transition-colors',
                    task.status === opt.value
                      ? 'bg-indigo-600 text-white border-indigo-600'
                      : 'border-gray-300 text-gray-600 hover:border-indigo-400'
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Description */}
          {task.description && (
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">説明</label>
              <p className="text-sm text-gray-700 whitespace-pre-wrap">{task.description}</p>
            </div>
          )}

          {/* Meta */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <span className="text-gray-500">優先度</span>
              <p className="font-medium capitalize">{task.priority}</p>
            </div>
            {task.due_date && (
              <div>
                <span className="text-gray-500">期限</span>
                <p className="font-medium">
                  {new Date(task.due_date).toLocaleDateString('ja-JP')}
                </p>
              </div>
            )}
          </div>

          {/* Tags */}
          {task.tags?.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {task.tags.map((tag: string) => (
                <span key={tag} className="text-xs bg-indigo-50 text-indigo-600 px-2 py-1 rounded-full">
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Comments */}
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-3">
              コメント ({task.comments?.length ?? 0})
            </label>
            <div className="space-y-3">
              {task.comments?.map((c: any) => (
                <div key={c.id} className="bg-gray-50 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-medium text-gray-700">{c.author_name}</span>
                    <span className="text-xs text-gray-400">
                      {new Date(c.created_at).toLocaleString('ja-JP', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                  <p className="text-sm text-gray-700 whitespace-pre-wrap">{c.content}</p>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Comment input */}
        <div className="p-4 border-t bg-gray-50">
          <div className="flex gap-2">
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="コメントを入力..."
              className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500"
              rows={2}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && e.metaKey && comment.trim()) {
                  addComment.mutate(comment.trim())
                }
              }}
            />
            <button
              onClick={() => comment.trim() && addComment.mutate(comment.trim())}
              disabled={!comment.trim() || addComment.isPending}
              className="self-end px-3 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-40"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
