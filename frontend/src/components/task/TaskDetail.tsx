import { useEffect, useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { X, Send, Pencil, Check, XCircle } from 'lucide-react'
import { api } from '../../api/client'
import clsx from 'clsx'
import type { Comment, Task, TaskPriority, TaskStatus } from '../../types'
import { STATUS_OPTIONS, PRIORITY_OPTIONS } from '../../constants/task'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { showErrorToast, showSuccessToast } from '../common/Toast'

interface Props {
  taskId: string
  projectId: string
  onClose: () => void
}

export default function TaskDetail({ taskId, projectId, onClose }: Props) {
  const qc = useQueryClient()
  const [comment, setComment] = useState('')

  // Editing state
  const [editingTitle, setEditingTitle] = useState(false)
  const [editingDescription, setEditingDescription] = useState(false)
  const [editingTags, setEditingTags] = useState(false)
  const [draftTitle, setDraftTitle] = useState('')
  const [draftDescription, setDraftDescription] = useState('')
  const [draftTags, setDraftTags] = useState('')
  const [draftDueDate, setDraftDueDate] = useState('')

  const titleInputRef = useRef<HTMLInputElement>(null)
  const descriptionRef = useRef<HTMLTextAreaElement>(null)
  const tagsInputRef = useRef<HTMLInputElement>(null)

  const { data: task } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => api.get(`/projects/${projectId}/tasks/${taskId}`).then((r) => r.data),
  })

  // Initialize draftDueDate when task loads
  useEffect(() => {
    if (task) {
      setDraftDueDate(task.due_date ? task.due_date.slice(0, 10) : '')
    }
  }, [task])

  const updateTask = useMutation({
    mutationFn: (data: Partial<Pick<Task, 'title' | 'description' | 'priority' | 'status' | 'due_date' | 'tags'>>) =>
      api.patch(`/projects/${projectId}/tasks/${taskId}`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      showSuccessToast('タスクを更新しました')
    },
    onError: () => {
      showErrorToast('タスクの更新に失敗しました')
    },
  })

  const updateFlags = useMutation({
    mutationFn: (flags: { needs_detail?: boolean; approved?: boolean }) =>
      api.patch(`/projects/${projectId}/tasks/${taskId}`, flags),
    onMutate: async (flags) => {
      await qc.cancelQueries({ queryKey: ['task', taskId] })
      await qc.cancelQueries({ queryKey: ['tasks', projectId] })
      const previousTask = qc.getQueryData<Task>(['task', taskId])
      const previousTasks = qc.getQueryData<Task[]>(['tasks', projectId])
      qc.setQueryData<Task>(['task', taskId], (old) =>
        old ? { ...old, ...flags } : old
      )
      qc.setQueryData<Task[]>(['tasks', projectId], (old) =>
        old?.map((t) => (t.id === taskId ? { ...t, ...flags } : t))
      )
      return { previousTask, previousTasks }
    },
    onError: (_err, _vars, context) => {
      if (context?.previousTask) {
        qc.setQueryData(['task', taskId], context.previousTask)
      }
      if (context?.previousTasks) {
        qc.setQueryData(['tasks', projectId], context.previousTasks)
      }
      showErrorToast('フラグの更新に失敗しました')
    },
    onSettled: () => {
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

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  // Title editing handlers
  const startEditTitle = () => {
    if (!task) return
    setDraftTitle(task.title)
    setEditingTitle(true)
    setTimeout(() => titleInputRef.current?.focus(), 0)
  }

  const saveTitle = () => {
    if (!draftTitle.trim() || !task) return
    if (draftTitle.trim() !== task.title) {
      updateTask.mutate({ title: draftTitle.trim() })
    }
    setEditingTitle(false)
  }

  const cancelEditTitle = () => {
    setEditingTitle(false)
  }

  // Description editing handlers
  const startEditDescription = () => {
    if (!task) return
    setDraftDescription(task.description ?? '')
    setEditingDescription(true)
    setTimeout(() => descriptionRef.current?.focus(), 0)
  }

  const saveDescription = () => {
    if (!task) return
    const newDesc = draftDescription.trim()
    if (newDesc !== (task.description ?? '')) {
      updateTask.mutate({ description: newDesc })
    }
    setEditingDescription(false)
  }

  const cancelEditDescription = () => {
    setEditingDescription(false)
  }

  // Tags editing handlers
  const startEditTags = () => {
    if (!task) return
    setDraftTags(task.tags?.join(', ') ?? '')
    setEditingTags(true)
    setTimeout(() => tagsInputRef.current?.focus(), 0)
  }

  const saveTags = () => {
    if (!task) return
    const newTags = draftTags ? draftTags.split(',').map((t) => t.trim()).filter(Boolean) : []
    const oldTags = task.tags ?? []
    if (JSON.stringify(newTags) !== JSON.stringify(oldTags)) {
      updateTask.mutate({ tags: newTags })
    }
    setEditingTags(false)
  }

  const cancelEditTags = () => {
    setEditingTags(false)
  }

  // Priority handler
  const handlePriorityChange = (priority: TaskPriority) => {
    if (!task || task.priority === priority) return
    updateTask.mutate({ priority })
  }

  // Status handler
  const handleStatusChange = (status: TaskStatus) => {
    if (!task || task.status === status) return
    updateTask.mutate({ status })
  }

  // Due date handler
  const handleDueDateChange = (value: string) => {
    setDraftDueDate(value)
    updateTask.mutate({ due_date: value ? new Date(value).toISOString() : null } as Record<string, string | null>)
  }

  const inputClasses = 'w-full border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500'
  const selectClasses = inputClasses

  if (!task) return null

  return (
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true" aria-label={task.title}>
      <div className="flex-1 bg-black/30" onClick={onClose} />
      <div className="w-full max-w-lg bg-white dark:bg-gray-800 shadow-xl dark:shadow-gray-900/50 flex flex-col h-full overflow-hidden">
        {/* Header */}
        <div className="flex items-start justify-between p-6 border-b border-gray-200 dark:border-gray-700">
          {editingTitle ? (
            <div className="flex-1 pr-4 flex items-center gap-2">
              <input
                ref={titleInputRef}
                type="text"
                value={draftTitle}
                onChange={(e) => setDraftTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') saveTitle()
                  if (e.key === 'Escape') cancelEditTitle()
                }}
                className={`${inputClasses} font-semibold`}
              />
              <button onClick={saveTitle} className="text-emerald-600 hover:text-emerald-700 dark:text-emerald-400 dark:hover:text-emerald-300" title="保存">
                <Check className="w-5 h-5" />
              </button>
              <button onClick={cancelEditTitle} className="text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300" title="キャンセル">
                <XCircle className="w-5 h-5" />
              </button>
            </div>
          ) : (
            <div className="flex-1 pr-4 flex items-start gap-2 group cursor-pointer" onClick={startEditTitle}>
              <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100 flex-1">{task.title}</h2>
              <button className="text-gray-300 dark:text-gray-600 group-hover:text-gray-500 dark:group-hover:text-gray-400 mt-0.5 flex-shrink-0" title="タイトルを編集">
                <Pencil className="w-4 h-4" />
              </button>
            </div>
          )}
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 flex-shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Status */}
          <div>
            <label className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">ステータス</label>
            <div className="flex flex-wrap gap-2">
              {STATUS_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => handleStatusChange(opt.value)}
                  className={clsx(
                    'px-3 py-1 text-sm rounded-full border transition-colors',
                    task.status === opt.value
                      ? 'bg-indigo-600 text-white border-indigo-600'
                      : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:border-indigo-400 dark:hover:border-indigo-500'
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Priority */}
          <div>
            <label className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">優先度</label>
            <select
              value={task.priority}
              onChange={(e) => handlePriorityChange(e.target.value as TaskPriority)}
              className={selectClasses}
            >
              {PRIORITY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {/* Due date */}
          <div>
            <label className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">期限</label>
            <input
              type="date"
              value={draftDueDate}
              onChange={(e) => handleDueDateChange(e.target.value)}
              className={inputClasses}
            />
          </div>

          {/* Review Flags */}
          <div>
            <label className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">レビューフラグ</label>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={task.needs_detail}
                  onChange={(e) => updateFlags.mutate({
                    needs_detail: e.target.checked,
                    ...(e.target.checked ? { approved: false } : {}),
                  })}
                  className="rounded border-amber-300 text-amber-600 focus:ring-amber-500"
                />
                <span className="text-sm text-amber-700 dark:text-amber-400">詳細要求</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={task.approved}
                  onChange={(e) => updateFlags.mutate({
                    approved: e.target.checked,
                    ...(e.target.checked ? { needs_detail: false } : {}),
                  })}
                  className="rounded border-emerald-300 text-emerald-600 focus:ring-emerald-500"
                />
                <span className="text-sm text-emerald-700 dark:text-emerald-400">実行許可</span>
              </label>
            </div>
          </div>

          {/* Description */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="block text-sm font-medium text-gray-600 dark:text-gray-400">説明</label>
              {!editingDescription && (
                <button
                  onClick={startEditDescription}
                  className="text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                  title="説明を編集"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {editingDescription ? (
              <div className="space-y-2">
                <textarea
                  ref={descriptionRef}
                  value={draftDescription}
                  onChange={(e) => setDraftDescription(e.target.value)}
                  rows={6}
                  className={`${inputClasses} resize-none`}
                  placeholder="説明を入力（Markdown対応）..."
                />
                <div className="flex gap-2 justify-end">
                  <button
                    onClick={cancelEditDescription}
                    className="px-3 py-1 text-sm text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700"
                  >
                    キャンセル
                  </button>
                  <button
                    onClick={saveDescription}
                    className="px-3 py-1 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
                  >
                    保存
                  </button>
                </div>
              </div>
            ) : task.description ? (
              <div className="cursor-pointer" onClick={startEditDescription}>
                <MarkdownRenderer>{task.description}</MarkdownRenderer>
              </div>
            ) : (
              <p
                className="text-sm text-gray-400 dark:text-gray-500 cursor-pointer hover:text-gray-500 dark:hover:text-gray-400"
                onClick={startEditDescription}
              >
                クリックして説明を追加...
              </p>
            )}
          </div>

          {/* Tags */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-medium text-gray-600 dark:text-gray-400">タグ</label>
              {!editingTags && (
                <button
                  onClick={startEditTags}
                  className="text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                  title="タグを編集"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {editingTags ? (
              <div className="space-y-2">
                <input
                  ref={tagsInputRef}
                  type="text"
                  value={draftTags}
                  onChange={(e) => setDraftTags(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') saveTags()
                    if (e.key === 'Escape') cancelEditTags()
                  }}
                  className={inputClasses}
                  placeholder="カンマ区切り（例: bug, frontend）"
                />
                <div className="flex gap-2 justify-end">
                  <button
                    onClick={cancelEditTags}
                    className="px-3 py-1 text-sm text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700"
                  >
                    キャンセル
                  </button>
                  <button
                    onClick={saveTags}
                    className="px-3 py-1 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
                  >
                    保存
                  </button>
                </div>
              </div>
            ) : task.tags?.length > 0 ? (
              <div className="flex flex-wrap gap-2 cursor-pointer" onClick={startEditTags}>
                {task.tags.map((tag: string) => (
                  <span key={tag} className="text-xs bg-indigo-50 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-400 px-2 py-1 rounded-full">
                    {tag}
                  </span>
                ))}
              </div>
            ) : (
              <p
                className="text-sm text-gray-400 dark:text-gray-500 cursor-pointer hover:text-gray-500 dark:hover:text-gray-400"
                onClick={startEditTags}
              >
                クリックしてタグを追加...
              </p>
            )}
          </div>

          {/* Comments */}
          <div>
            <label className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-3">
              コメント ({task.comments?.length ?? 0})
            </label>
            <div className="space-y-3">
              {task.comments?.map((c: Comment) => (
                <div key={c.id} className="bg-gray-50 dark:bg-gray-700 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs font-medium text-gray-700 dark:text-gray-200">{c.author_name}</span>
                    <span className="text-xs text-gray-400 dark:text-gray-500">
                      {new Date(c.created_at).toLocaleString('ja-JP', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                  <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{c.content}</p>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Comment input */}
        <div className="p-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-750 dark:bg-gray-900/50">
          <div className="flex gap-2">
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="コメントを入力..."
              className="flex-1 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
