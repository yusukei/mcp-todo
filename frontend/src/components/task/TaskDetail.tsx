import { useEffect, useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { qk } from '../../api/queryKeys'
import { X, Pencil, Check, XCircle, ChevronUp, ImagePlus, Trash2, Copy, ShieldCheck, ShieldOff } from 'lucide-react'
import { api } from '../../api/client'
import clsx from 'clsx'
import type { Attachment, DecisionContext, Task, TaskPriority, TaskStatus, TaskType } from '../../types'
import { STATUS_OPTIONS, PRIORITY_OPTIONS, TASK_TYPE_OPTIONS } from '../../constants/task'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { showErrorToast, showSuccessToast } from '../common/Toast'
import { TaskCommentList, TaskCommentInput } from './TaskCommentSection'
import TaskLinksSection from './TaskLinksSection'
import TaskSubtaskSection from './TaskSubtaskSection'
import CopyUrlButton from '../common/CopyUrlButton'

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
      recommendation: dc?.recommendation ?? null,
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

  // Phase 5: A recommended option's label (case-insensitive substring
  // match) gets highlighted with the approved (green) ring. The author
  // free-form text in `recommendation`, so we substring-match rather
  // than insisting on exact equality.
  const recommendedLabel = dc?.recommendation?.trim().toLowerCase() ?? ''
  const isRecommended = (opt: { label: string }) =>
    recommendedLabel.length > 0 &&
    recommendedLabel.includes(opt.label.trim().toLowerCase())

  return (
    <div className="border-l-4 border-decision rounded-comfortable p-4 bg-decision/10">
      <div className="flex items-center justify-between mb-3">
        <label className="block text-sm font-medium text-decision font-serif">判断コンテキスト</label>
        {!editing && (
          <button
            onClick={startEdit}
            className="text-decision hover:opacity-80"
            title="判断コンテキストを編集"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {editing ? (
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-decision mb-1">背景</label>
            <textarea
              value={draftBackground}
              onChange={(e) => setDraftBackground(e.target.value)}
              rows={3}
              className={`${inputClasses} resize-none`}
              placeholder="課題に関する背景情報..."
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-decision mb-1">判断事項</label>
            <textarea
              value={draftDecisionPoint}
              onChange={(e) => setDraftDecisionPoint(e.target.value)}
              rows={2}
              className={`${inputClasses} resize-none`}
              placeholder="ユーザが判断すべき箇所..."
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-decision mb-1">選択肢</label>
            <div className="space-y-2">
              {draftOptions.map((opt, i) => (
                <div key={i} className="flex gap-2 items-start">
                  <span className="text-xs text-decision mt-2.5 w-5 text-center flex-shrink-0 font-mono">{i + 1}.</span>
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
                      className="text-pri-urgent hover:opacity-80 mt-2"
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
              className="mt-2 text-xs text-decision hover:opacity-80"
            >
              + 選択肢を追加
            </button>
          </div>
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => setEditing(false)}
              className="px-3 py-1 text-sm text-gray-200 border border-gray-600 rounded-comfortable hover:bg-gray-700"
            >
              キャンセル
            </button>
            <button
              onClick={save}
              className="px-3 py-1 text-sm text-gray-50 bg-decision rounded-comfortable hover:opacity-90"
            >
              保存
            </button>
          </div>
        </div>
      ) : dc && (dc.background || dc.decision_point || dc.options?.length) ? (
        <div className="space-y-3 cursor-pointer" onClick={startEdit}>
          {dc.background && (
            <div>
              <span className="text-xs font-medium text-decision">背景</span>
              <p className="text-sm text-gray-100 mt-1 whitespace-pre-wrap">{dc.background}</p>
            </div>
          )}
          {dc.decision_point && (
            <div>
              <span className="text-xs font-medium text-decision">判断事項</span>
              <p className="text-sm text-gray-100 mt-1 whitespace-pre-wrap">{dc.decision_point}</p>
            </div>
          )}
          {dc.options?.length > 0 && (
            <div>
              <span className="text-xs font-medium text-decision">選択肢</span>
              <ol className="mt-2 space-y-2">
                {dc.options.map((opt, i) => {
                  const recommended = isRecommended(opt)
                  return (
                    <li
                      key={i}
                      className={clsx(
                        'flex items-start gap-2 p-3 rounded-comfortable border transition-colors',
                        recommended
                          ? 'bg-approved/15 border-approved'
                          : 'bg-gray-800/60 border-gray-700',
                      )}
                    >
                      <span className={clsx(
                        'text-sm font-semibold mt-0.5 font-mono flex-shrink-0',
                        recommended ? 'text-approved' : 'text-decision',
                      )}>
                        {recommended ? '★' : `${i + 1}.`}
                      </span>
                      <div className="flex-1">
                        <span className="text-sm font-medium text-gray-50">{opt.label}</span>
                        {opt.description && (
                          <p className="text-xs text-gray-300 mt-0.5">{opt.description}</p>
                        )}
                        {recommended && (
                          <span className="inline-block mt-1.5 text-[10px] font-mono uppercase tracking-wider text-approved">
                            推奨
                          </span>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ol>
            </div>
          )}
          {dc.recommendation && (
            <div className="border-l-2 border-approved pl-3 py-1 bg-approved/5 rounded-r">
              <span className="text-xs font-medium text-approved">推奨</span>
              <p className="text-sm text-gray-100 mt-1 whitespace-pre-wrap">{dc.recommendation}</p>
            </div>
          )}
        </div>
      ) : (
        <p
          className="text-sm text-decision/70 cursor-pointer hover:text-decision"
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
  /**
   * Layout mode. ``slideOver`` (default) renders the legacy fixed-
   * position modal with backdrop — used by ProjectPage and as the
   * fallback when no TaskDetailPane is in the Workbench layout.
   * ``pane`` strips the modal chrome and renders flush inside its
   * parent (used by TaskDetailPane).
   */
  displayMode?: 'slideOver' | 'pane'
  /**
   * Phase 5: when ``true``, the meta fields (status / priority / due
   * date / review flag / task type) move into a 260 px right-hand
   * rail and the main column shows only narrative content (decision
   * context, description, tags, completion report, attachments,
   * links, subtasks, comments). Only meaningful in ``pane`` mode —
   * slide-over keeps the legacy single-column layout because the
   * modal is too narrow for a side rail.
   */
  metaRail?: boolean
}

export default function TaskDetail({ taskId, projectId, onClose, onNavigateTask, displayMode = 'slideOver', metaRail = false }: Props) {
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
    queryKey: qk.task(taskId),
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
      qc.invalidateQueries({ queryKey: qk.tasksInProject(projectId) })
      qc.invalidateQueries({ queryKey: qk.task(taskId) })
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
      await qc.cancelQueries({ queryKey: qk.task(taskId) })
      await qc.cancelQueries({ queryKey: qk.tasksInProject(projectId) })
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
      qc.invalidateQueries({ queryKey: qk.tasksInProject(projectId) })
      qc.invalidateQueries({ queryKey: qk.task(taskId) })
    },
  })

  // Fetch parent task by ID (only when needed)
  const { data: parentTask } = useQuery<Task>({
    queryKey: qk.task(task?.parent_task_id),
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
      return api.post(`/projects/${projectId}/tasks/${taskId}/attachments`, formData)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.task(taskId) })
      qc.invalidateQueries({ queryKey: qk.tasksInProject(projectId) })
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
      qc.invalidateQueries({ queryKey: qk.task(taskId) })
      qc.invalidateQueries({ queryKey: qk.tasksInProject(projectId) })
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

  const inputClasses = 'w-full border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus'
  const selectClasses = inputClasses

  if (!task) return null

  // Layout chrome differs by displayMode: slide-over uses fixed
  // positioning + backdrop (legacy ProjectPage modal), pane mode
  // renders flush in its parent (no backdrop, fills 100% height).
  const isPane = displayMode === 'pane'
  // metaRail only applies in pane mode — slide-over modal is too
  // narrow (max-w-3xl) for a side rail to be useful.
  const useMetaRail = isPane && metaRail
  const outerClass = isPane
    ? 'h-full flex flex-col bg-gray-100 dark:bg-gray-800'
    : 'fixed inset-0 z-50 flex items-center justify-center p-4'
  const panelClass = isPane
    ? 'relative h-full w-full flex flex-col overflow-hidden'
    : 'relative w-full max-w-3xl max-h-[90vh] bg-gray-100 dark:bg-gray-800 shadow-xl dark:shadow-gray-900/50 flex flex-col overflow-hidden rounded-xl'

  return (
    <div className={outerClass} role={isPane ? undefined : 'dialog'} aria-modal={isPane ? undefined : 'true'} aria-label={isPane ? undefined : task.title}>
      {!isPane && <div className="fixed inset-0 bg-black/30" onClick={onClose} />}
      <div className={panelClass}>
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
          <div className="flex items-center gap-1 flex-shrink-0">
            <CopyUrlButton
              kind="task"
              contextProjectId={task.project_id}
              resourceId={task.id}
              title={task.title}
              variant="always-visible"
              size="md"
            />
            <button
              onClick={() => {
                navigator.clipboard.writeText(task.id)
                showSuccessToast('タスクIDをコピーしました')
              }}
              className="text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 p-1 rounded transition-colors"
              title={`ID: ${task.id}`}
            >
              <Copy className="w-4 h-4" />
            </button>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Parent task link */}
        {parentTask && (
          <div className="px-6 pt-3 pb-0">
            <button
              onClick={() => onNavigateTask?.(parentTask.id)}
              className="flex items-center gap-1.5 text-xs text-accent-600 dark:text-accent-400 hover:text-accent-800 dark:hover:text-accent-300 transition-colors"
            >
              <ChevronUp className="w-3.5 h-3.5" />
              <span>親タスク: {parentTask.title}</span>
            </button>
          </div>
        )}

        {/* Body — wrapped in 2-column layout when metaRail is on */}
        <div className={clsx('flex-1 overflow-hidden', useMetaRail ? 'flex' : 'flex flex-col')}>
        <div className={clsx('flex-1 overflow-y-auto p-6 space-y-6', useMetaRail && 'min-w-0')}>
          {/* Status: P1-A 修正で metaRail でも main column に残す。
              metaRail は設計プロト variant-b.jsx:101-174 の「担当/判断者
              /関連タスク/添付/履歴」5 セクションに専念し、メタフィー
              ルドの編集は main column の通常位置に置く。 */}
          {true && (
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">ステータス</label>
            <div className="flex flex-wrap gap-2">
              {STATUS_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => handleStatusChange(opt.value)}
                  className={clsx(
                    'px-3 py-1 text-sm rounded-full border transition-colors',
                    task.status === opt.value
                      ? 'bg-accent-500 text-gray-50 border-accent-600'
                      : 'border-gray-600 text-gray-200 hover:border-accent-500'
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
          )}

          {/* Priority & Due date (P1-A: 常時表示) */}
          {true && (
          <div className="flex items-center gap-4">
            <div className="w-40">
              <label className="block text-sm font-medium text-gray-300 mb-2">優先度</label>
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
              <label className="block text-sm font-medium text-gray-300 mb-2">期限</label>
              <input
                type="date"
                value={draftDueDate}
                onChange={(e) => handleDueDateChange(e.target.value)}
                className={inputClasses}
              />
            </div>
          </div>
          )}

          {/* Review Flags (P1-A: 常時表示) */}
          {true && (
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">レビューフラグ</label>
            <div className="flex gap-4">
              <button
                onClick={() => updateFlags.mutate({ approved: !task.approved })}
                className={clsx(
                  'inline-flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-full border transition-all',
                  task.approved
                    ? 'bg-approved/15 text-approved border-approved/40 shadow-sm'
                    : 'bg-gray-700/50 text-gray-300 border-gray-600 hover:bg-gray-700',
                )}
                aria-label={task.approved ? '実行許可を取消' : '実行許可を付与'}
              >
                {task.approved ? <ShieldCheck className="w-4 h-4" /> : <ShieldOff className="w-4 h-4" />}
                実行許可
              </button>
            </div>
          </div>
          )}

          {/* Task Type (P1-A: 常時表示) */}
          {true && (
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">タスク種別</label>
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
                        ? 'bg-decision text-gray-50 border-decision'
                        : 'bg-accent-500 text-gray-50 border-accent-600'
                      : 'border-gray-600 text-gray-200 hover:border-accent-500'
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
          )}

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
                    className="px-3 py-1 text-sm text-white bg-accent-600 rounded-lg hover:bg-accent-600"
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
                    className="px-3 py-1 text-sm text-white bg-accent-600 rounded-lg hover:bg-accent-600"
                  >
                    保存
                  </button>
                </div>
              </div>
            ) : task.tags?.length > 0 ? (
              <div className="flex flex-wrap gap-2 cursor-pointer" onClick={startEditTags}>
                {task.tags.map((tag: string) => (
                  <span key={tag} className="text-xs bg-accent-50 dark:bg-accent-900/40 text-accent-600 dark:text-accent-400 px-2 py-1 rounded-full">
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
            <div className="border-l-4 border-approved rounded-comfortable p-4 bg-approved/10">
              <div className="flex items-center justify-between mb-2">
                <label className="block text-sm font-medium text-approved font-serif">完了レポート</label>
                {!editingCompletionReport && (
                  <button
                    onClick={() => {
                      setDraftCompletionReport(task.completion_report ?? '')
                      setEditingCompletionReport(true)
                      setTimeout(() => completionReportRef.current?.focus(), 0)
                    }}
                    className="text-approved hover:opacity-80"
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
                      className="px-3 py-1 text-sm text-gray-200 border border-gray-600 rounded-comfortable hover:bg-gray-700"
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
                      className="px-3 py-1 text-sm text-gray-50 bg-approved rounded-comfortable hover:opacity-90"
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
                  className="text-sm text-approved/70 cursor-pointer hover:text-approved"
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
                className="flex items-center gap-1 text-xs text-accent-600 dark:text-accent-400 hover:text-accent-800 dark:hover:text-accent-300 transition-colors disabled:opacity-40"
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
                      className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-pri-urgent text-gray-50 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:opacity-90"
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

          {/* Task links (blocks / blocked_by) */}
          <TaskLinksSection task={task} projectId={projectId} onTaskClick={onNavigateTask} />

          {/* Subtasks */}
          <TaskSubtaskSection task={task} projectId={projectId} onTaskClick={onNavigateTask} />

          {/* Comments */}
          <TaskCommentList task={task} />
        </div>

        {/* P1-A: metaRail を設計プロト variant-b.jsx:101-174 準拠の
            5 セクションに整理 (担当 / 判断者 / 関連タスク / 添付 / 履歴)。
            ステータス・優先度・タスク種別の編集 UI は main column 側
            (上部) に常時表示する。 */}
        {useMetaRail && (
          <MetaRail task={task} onNavigateTask={onNavigateTask} />
        )}
        </div>

        {/* Comment input */}
        <TaskCommentInput task={task} projectId={projectId} />
      </div>
    </div>
  )
}

// ── MetaRail (P1-A) ───────────────────────────────────────────────
//
// 設計プロト variant-b.jsx:101-174 の右側 260px metaRail を再現。
// 5 セクション: 担当 / 判断者 / 関連タスク / 添付 / 履歴。
// 既存 task オブジェクトから取れる情報のみ表示し、追加 API 呼び出し
// は行わない (関連タスク id 一覧は表示するが title 解決は省略)。
function MetaRail({
  task,
  onNavigateTask,
}: {
  task: Task
  onNavigateTask?: (taskId: string) => void
}) {
  const labelClass =
    'mb-2 block text-[10.5px] font-mono uppercase tracking-[0.12em] text-gray-300 font-semibold'
  const sectionClass = 'mb-5'

  const initial = (s: string | null | undefined) =>
    (s ?? '?').trim().charAt(0).toUpperCase() || '?'

  const shortId = (id: string) =>
    id ? `T${id.replace(/[^0-9a-zA-Z]/g, '').slice(-6).toUpperCase()}` : ''

  const formatRelative = (iso: string | null | undefined): string => {
    if (!iso) return ''
    const t = new Date(iso).getTime()
    if (Number.isNaN(t)) return ''
    const diff = Date.now() - t
    const m = 60_000, h = 60 * m, d = 24 * h
    if (diff < h) return `${Math.max(1, Math.floor(diff / m))}m`
    if (diff < d) return `${Math.floor(diff / h)}h`
    return `${Math.floor(diff / d)}d`
  }

  const relatedIds = [
    ...(task.blocked_by ?? []),
    ...(task.blocks ?? []),
  ].slice(0, 6)

  // 履歴: ActivityEntry が無いので created/updated/completed の 3 点
  // のみ復元 (Phase 0.5 で追加された actor_type を将来的に取り込む拡張点)
  const historyEntries: Array<{
    who: string
    what: string
    when: string
  }> = []
  if (task.completed_at) {
    historyEntries.push({
      who: 'system',
      what: 'タスクを完了',
      when: formatRelative(task.completed_at),
    })
  }
  if (
    task.updated_at &&
    task.updated_at !== task.created_at &&
    task.updated_at !== task.completed_at
  ) {
    historyEntries.push({
      who: 'system',
      what: '更新',
      when: formatRelative(task.updated_at),
    })
  }
  historyEntries.push({
    who: 'system',
    what: 'タスク作成',
    when: formatRelative(task.created_at),
  })

  return (
    <aside
      className="w-[260px] flex-shrink-0 border-l border-line-2 bg-gray-950 overflow-y-auto p-5 text-[12px] text-gray-100"
      aria-label="タスクメタ情報"
    >
      {/* 1. 担当 */}
      {task.assignee_name && (
        <div className={sectionClass}>
          <div className={labelClass}>担当</div>
          <div className="flex items-center gap-2">
            <span className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-full bg-accent-500 text-[10px] font-semibold text-gray-50">
              {initial(task.assignee_name)}
            </span>
            <span className="text-gray-50">{task.assignee_name}</span>
          </div>
        </div>
      )}

      {/* 2. 判断者 (decision タスクのみ) */}
      {task.task_type === 'decision' && task.decider_name && (
        <div className={sectionClass}>
          <div className={labelClass}>判断者</div>
          <div className="mb-1 flex items-center gap-2">
            <span className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-full bg-decision text-[10px] font-semibold text-gray-50">
              {initial(task.decider_name)}
            </span>
            <span className="text-gray-50">{task.decider_name}</span>
          </div>
          {task.decision_requested_at && (
            <div className="pl-[30px] text-[11px] text-gray-300">
              応答待ち · {formatRelative(task.decision_requested_at)}
            </div>
          )}
        </div>
      )}

      {/* 3. 関連タスク */}
      {relatedIds.length > 0 && (
        <div className={sectionClass}>
          <div className={labelClass}>関連タスク</div>
          <div className="flex flex-col gap-1">
            {relatedIds.map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => onNavigateTask?.(id)}
                className="flex items-center gap-1.5 rounded px-1 py-1 text-left text-[12px] text-gray-100 hover:bg-gray-800"
              >
                <span aria-hidden className="status-dot todo" />
                <span className="flex-1 truncate">関連タスク</span>
                <span className="font-mono text-[10px] text-gray-300">
                  {shortId(id)}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 4. 添付 */}
      {task.attachments && task.attachments.length > 0 && (
        <div className={sectionClass}>
          <div className={labelClass}>添付 ({task.attachments.length})</div>
          <div className="flex flex-col gap-1">
            {task.attachments.map((a) => (
              <div
                key={a.id}
                className="flex items-center gap-1.5 rounded bg-gray-700 px-2 py-1.5 text-[12px] text-gray-100"
              >
                <span aria-hidden className="text-gray-300">📎</span>
                <span className="flex-1 truncate">{a.filename}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 5. 履歴 */}
      <div>
        <div className={labelClass}>履歴</div>
        <div className="relative flex flex-col gap-2.5 pl-3.5">
          <div
            aria-hidden
            className="absolute left-1 top-1 bottom-1 w-px bg-line-2"
          />
          {historyEntries.map((h, i) => (
            <div key={i} className="relative text-[11.5px]">
              <span
                aria-hidden
                className="absolute -left-3.5 top-[5px] h-[9px] w-[9px] rounded-full border-[1.5px] border-gray-300 bg-gray-950"
              />
              <div className="text-gray-100">
                <b className="font-semibold text-gray-50">{h.who}</b> {h.what}
              </div>
              <div className="text-[10.5px] text-gray-400">{h.when} ago</div>
            </div>
          ))}
        </div>
      </div>
    </aside>
  )
}
