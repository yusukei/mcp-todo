import { useEffect, useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { X, Pencil, Check, XCircle, ChevronUp, ImagePlus, Trash2 } from 'lucide-react'
import { api } from '../../api/client'
import clsx from 'clsx'
import type { Attachment, DecisionContext, Task, TaskPriority, TaskStatus, TaskType } from '../../types'
import { STATUS_OPTIONS, PRIORITY_OPTIONS, TASK_TYPE_OPTIONS } from '../../constants/task'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { showErrorToast, showSuccessToast } from '../common/Toast'
import { TaskCommentList, TaskCommentInput } from './TaskCommentSection'
import TaskSubtaskSection from './TaskSubtaskSection'

function DecisionContextSection({
  task,
  onUpdate,
  inputClasses,
}: {
  task: Task
  onUpdate: (dc: DecisionContext | null) => void
  inputClasses: string
}) {
  const dc = task.decision_context
  const [editing, setEditing] = useState(false)
  const [draftBackground, setDraftBackground] = useState('')
  const [draftDecisionPoint, setDraftDecisionPoint] = useState('')
  const [draftOptions, setDraftOptions] = useState<{ label: string; description: string }[]>([])

  const startEdit = () => {
    setDraftBackground(dc?.background ?? '')
    setDraftDecisionPoint(dc?.decision_point ?? '')
    setDraftOptions(dc?.options?.length ? dc.options.map((o) => ({ ...o })) : [{ label: '', description: '' }])
    setEditing(true)
  }

  const save = () => {
    const filtered = draftOptions.filter((o) => o.label.trim())
    onUpdate({
      background: draftBackground.trim(),
      decision_point: draftDecisionPoint.trim(),
      options: filtered,
    })
    setEditing(false)
  }

  const addOption = () => setDraftOptions([...draftOptions, { label: '', description: '' }])
  const removeOption = (i: number) => setDraftOptions(draftOptions.filter((_, idx) => idx !== i))
  const updateOption = (i: number, field: 'label' | 'description', value: string) => {
    const copy = [...draftOptions]
    copy[i] = { ...copy[i], [field]: value }
    setDraftOptions(copy)
  }

  return (
    <div className="border border-violet-200 dark:border-violet-800 rounded-lg p-4 bg-violet-50/50 dark:bg-violet-900/20">
      <div className="flex items-center justify-between mb-3">
        <label className="block text-sm font-medium text-violet-700 dark:text-violet-400">判断コンテキスト</label>
        {!editing && (
          <button
            onClick={startEdit}
            className="text-violet-500 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300"
            title="判断コンテキストを編集"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {editing ? (
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-violet-600 dark:text-violet-400 mb-1">背景</label>
            <textarea
              value={draftBackground}
              onChange={(e) => setDraftBackground(e.target.value)}
              rows={3}
              className={`${inputClasses} resize-none`}
              placeholder="課題に関する背景情報..."
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-violet-600 dark:text-violet-400 mb-1">判断事項</label>
            <textarea
              value={draftDecisionPoint}
              onChange={(e) => setDraftDecisionPoint(e.target.value)}
              rows={2}
              className={`${inputClasses} resize-none`}
              placeholder="ユーザが判断すべき箇所..."
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-violet-600 dark:text-violet-400 mb-1">選択肢</label>
            <div className="space-y-2">
              {draftOptions.map((opt, i) => (
                <div key={i} className="flex gap-2 items-start">
                  <span className="text-xs text-violet-400 mt-2.5 w-5 text-center flex-shrink-0">{i + 1}.</span>
                  <div className="flex-1 space-y-1">
                    <input
                      type="text"
                      value={opt.label}
                      onChange={(e) => updateOption(i, 'label', e.target.value)}
                      className={inputClasses}
                      placeholder="選択肢名"
                    />
                    <input
                      type="text"
                      value={opt.description}
                      onChange={(e) => updateOption(i, 'description', e.target.value)}
                      className={`${inputClasses} text-xs`}
                      placeholder="説明（任意）"
                    />
                  </div>
                  {draftOptions.length > 1 && (
                    <button
                      onClick={() => removeOption(i)}
                      className="text-red-400 hover:text-red-600 mt-2"
                      title="削除"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  )}
                </div>
              ))}
            </div>
            <button
              onClick={addOption}
              className="mt-2 text-xs text-violet-600 dark:text-violet-400 hover:text-violet-800 dark:hover:text-violet-300"
            >
              + 選択肢を追加
            </button>
          </div>
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => setEditing(false)}
              className="px-3 py-1 text-sm text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              キャンセル
            </button>
            <button
              onClick={save}
              className="px-3 py-1 text-sm text-white bg-violet-600 rounded-lg hover:bg-violet-700"
            >
              保存
            </button>
          </div>
        </div>
      ) : dc && (dc.background || dc.decision_point || dc.options?.length) ? (
        <div className="space-y-3 cursor-pointer" onClick={startEdit}>
          {dc.background && (
            <div>
              <span className="text-xs font-medium text-violet-600 dark:text-violet-400">背景</span>
              <MarkdownRenderer className="prose prose-sm prose-violet dark:prose-invert max-w-none mt-1">{dc.background}</MarkdownRenderer>
            </div>
          )}
          {dc.decision_point && (
            <div>
              <span className="text-xs font-medium text-violet-600 dark:text-violet-400">判断事項</span>
              <MarkdownRenderer className="prose prose-sm prose-violet dark:prose-invert max-w-none mt-1">{dc.decision_point}</MarkdownRenderer>
            </div>
          )}
          {dc.options?.length > 0 && (
            <div>
              <span className="text-xs font-medium text-violet-600 dark:text-violet-400">選択肢</span>
              <ol className="mt-1 space-y-1.5">
                {dc.options.map((opt, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className="text-sm font-semibold text-violet-500 mt-0.5">{i + 1}.</span>
                    <div>
                      <span className="text-sm font-medium text-gray-800 dark:text-gray-100">{opt.label}</span>
                      {opt.description && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{opt.description}</p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      ) : (
        <p
          className="text-sm text-violet-400 dark:text-violet-500 cursor-pointer hover:text-violet-600 dark:hover:text-violet-400"
          onClick={startEdit}
        >
          クリックして判断コンテキストを追加...
        </p>
      )}
    </div>
  )
}

interface Props {
  taskId: string
  projectId: string
  onClose: () => void
  onNavigateTask?: (taskId: string) => void
}

export default function TaskDetail({ taskId, projectId, onClose, onNavigateTask }: Props) {
  const qc = useQueryClient()

  // Editing state
  const [editingTitle, setEditingTitle] = useState(false)
  const [editingDescription, setEditingDescription] = useState(false)
  const [editingTags, setEditingTags] = useState(false)
  const [editingCompletionReport, setEditingCompletionReport] = useState(false)
  const [draftTitle, setDraftTitle] = useState('')
  const [draftDescription, setDraftDescription] = useState('')
  const [draftTags, setDraftTags] = useState('')
  const [draftDueDate, setDraftDueDate] = useState('')
  const [draftCompletionReport, setDraftCompletionReport] = useState('')

  const titleInputRef = useRef<HTMLInputElement>(null)
  const descriptionRef = useRef<HTMLTextAreaElement>(null)
  const tagsInputRef = useRef<HTMLInputElement>(null)
  const completionReportRef = useRef<HTMLTextAreaElement>(null)

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
    mutationFn: (data: Record<string, unknown>) =>
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

  // Fetch parent task by ID (only when needed)
  const { data: parentTask } = useQuery<Task>({
    queryKey: ['task', task?.parent_task_id],
    queryFn: () => api.get(`/projects/${projectId}/tasks/${task!.parent_task_id}`).then((r) => r.data),
    enabled: !!task?.parent_task_id,
  })

  // Attachment state
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  const uploadAttachment = useMutation({
    mutationFn: (file: File) => {
      const formData = new FormData()
      formData.append('file', file)
      return api.post(`/projects/${projectId}/tasks/${taskId}/attachments`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
      showSuccessToast('画像を添付しました')
    },
    onError: () => {
      showErrorToast('画像の添付に失敗しました')
    },
  })

  const deleteAttachment = useMutation({
    mutationFn: (attachmentId: string) =>
      api.delete(`/projects/${projectId}/tasks/${taskId}/attachments/${attachmentId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', taskId] })
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
      showSuccessToast('添付画像を削除しました')
    },
    onError: () => {
      showErrorToast('添付画像の削除に失敗しました')
    },
  })

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      uploadAttachment.mutate(file)
      e.target.value = ''
    }
  }

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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={task.title}>
      <div className="fixed inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-3xl max-h-[90vh] bg-white dark:bg-gray-800 shadow-xl dark:shadow-gray-900/50 flex flex-col overflow-hidden rounded-xl">
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

        {/* Parent task link */}
        {parentTask && (
          <div className="px-6 pt-3 pb-0">
            <button
              onClick={() => onNavigateTask?.(parentTask.id)}
              className="flex items-center gap-1.5 text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 dark:hover:text-indigo-300 transition-colors"
            >
              <ChevronUp className="w-3.5 h-3.5" />
              <span>親タスク: {parentTask.title}</span>
            </button>
          </div>
        )}

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

          {/* Priority & Due date */}
          <div className="flex items-center gap-4">
            <div className="w-40">
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
            <div className="flex-1">
              <label className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">期限</label>
              <input
                type="date"
                value={draftDueDate}
                onChange={(e) => handleDueDateChange(e.target.value)}
                className={inputClasses}
              />
            </div>
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

          {/* Task Type */}
          <div>
            <label className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-2">タスク種別</label>
            <div className="flex flex-wrap gap-2">
              {TASK_TYPE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => {
                    if (task.task_type === opt.value) return
                    updateTask.mutate({ task_type: opt.value })
                  }}
                  className={clsx(
                    'px-3 py-1 text-sm rounded-full border transition-colors',
                    task.task_type === opt.value
                      ? opt.value === 'decision'
                        ? 'bg-violet-600 text-white border-violet-600'
                        : 'bg-indigo-600 text-white border-indigo-600'
                      : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:border-indigo-400 dark:hover:border-indigo-500'
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Decision Context */}
          {task.task_type === 'decision' && (
            <DecisionContextSection
              task={task}
              onUpdate={(dc) => updateTask.mutate({ decision_context: dc })}
              inputClasses={inputClasses}
            />
          )}

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

          {/* Completion Report */}
          {task.status === 'done' && (
            <div className="border border-emerald-200 dark:border-emerald-800 rounded-lg p-4 bg-emerald-50/50 dark:bg-emerald-900/20">
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-emerald-700 dark:text-emerald-400">完了レポート</label>
                {!editingCompletionReport && (
                  <button
                    onClick={() => {
                      setDraftCompletionReport(task.completion_report ?? '')
                      setEditingCompletionReport(true)
                      setTimeout(() => completionReportRef.current?.focus(), 0)
                    }}
                    className="text-emerald-500 hover:text-emerald-700 dark:text-emerald-400 dark:hover:text-emerald-300"
                    title="完了レポートを編集"
                  >
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
              {editingCompletionReport ? (
                <div className="space-y-2">
                  <textarea
                    ref={completionReportRef}
                    value={draftCompletionReport}
                    onChange={(e) => setDraftCompletionReport(e.target.value)}
                    rows={6}
                    className={`${inputClasses} resize-none`}
                    placeholder="完了レポートを入力（Markdown対応）..."
                  />
                  <div className="flex gap-2 justify-end">
                    <button
                      onClick={() => setEditingCompletionReport(false)}
                      className="px-3 py-1 text-sm text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700"
                    >
                      キャンセル
                    </button>
                    <button
                      onClick={() => {
                        const newReport = draftCompletionReport.trim()
                        if (newReport !== (task.completion_report ?? '')) {
                          updateTask.mutate({ completion_report: newReport || null })
                        }
                        setEditingCompletionReport(false)
                      }}
                      className="px-3 py-1 text-sm text-white bg-emerald-600 rounded-lg hover:bg-emerald-700"
                    >
                      保存
                    </button>
                  </div>
                </div>
              ) : task.completion_report ? (
                <div
                  className="cursor-pointer"
                  onClick={() => {
                    setDraftCompletionReport(task.completion_report ?? '')
                    setEditingCompletionReport(true)
                    setTimeout(() => completionReportRef.current?.focus(), 0)
                  }}
                >
                  <MarkdownRenderer>{task.completion_report}</MarkdownRenderer>
                </div>
              ) : (
                <p
                  className="text-sm text-emerald-400 dark:text-emerald-500 cursor-pointer hover:text-emerald-600 dark:hover:text-emerald-400"
                  onClick={() => {
                    setDraftCompletionReport('')
                    setEditingCompletionReport(true)
                    setTimeout(() => completionReportRef.current?.focus(), 0)
                  }}
                >
                  クリックして完了レポートを追加...
                </p>
              )}
            </div>
          )}

          {/* Attachments */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-medium text-gray-600 dark:text-gray-400">
                添付画像 ({task.attachments?.length ?? 0})
              </label>
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadAttachment.isPending}
                className="flex items-center gap-1 text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 dark:hover:text-indigo-300 transition-colors disabled:opacity-40"
              >
                <ImagePlus className="w-3.5 h-3.5" />
                {uploadAttachment.isPending ? 'アップロード中...' : '画像を追加'}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/gif,image/webp"
                onChange={handleFileSelect}
                className="hidden"
              />
            </div>
            {task.attachments?.length > 0 ? (
              <div className="flex flex-wrap gap-3">
                {task.attachments.map((a: Attachment) => (
                  <div key={a.id} className="relative group">
                    <img
                      src={`/api/v1/attachments/${taskId}/${a.filename}`}
                      alt={a.filename}
                      className="w-20 h-20 object-cover rounded-lg border border-gray-200 dark:border-gray-600 cursor-pointer hover:opacity-80 transition-opacity"
                      onClick={() => setPreviewUrl(`/api/v1/attachments/${taskId}/${a.filename}`)}
                    />
                    <button
                      onClick={() => deleteAttachment.mutate(a.id)}
                      className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-500 text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-600"
                      title="削除"
                      aria-label="削除"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-400 dark:text-gray-500">添付画像はありません</p>
            )}
          </div>

          {/* Image preview modal */}
          {previewUrl && (
            <div
              className="fixed inset-0 z-[60] bg-black/70 flex items-center justify-center"
              onClick={() => setPreviewUrl(null)}
              onKeyDown={(e) => { if (e.key === 'Escape') setPreviewUrl(null) }}
              role="dialog"
              aria-modal="true"
              aria-label="画像プレビュー"
            >
              <img
                src={previewUrl}
                alt="preview"
                className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg shadow-2xl"
                onClick={(e) => e.stopPropagation()}
              />
              <button
                onClick={() => setPreviewUrl(null)}
                className="absolute top-4 right-4 text-white hover:text-gray-300"
                aria-label="閉じる"
              >
                <X className="w-8 h-8" />
              </button>
            </div>
          )}

          {/* Subtasks */}
          <TaskSubtaskSection task={task} projectId={projectId} onTaskClick={onNavigateTask} />

          {/* Comments */}
          <TaskCommentList task={task} />
        </div>

        {/* Comment input */}
        <TaskCommentInput task={task} projectId={projectId} />
      </div>
    </div>
  )
}
