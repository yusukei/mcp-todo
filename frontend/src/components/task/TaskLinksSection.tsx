import { useMemo, useState, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock, Plus, X } from 'lucide-react'
import clsx from 'clsx'
import { api, ApiError } from '../../api/client'
import type { Task } from '../../types'
import { STATUS_COLORS, STATUS_LABELS, PRIORITY_DOT_COLORS } from '../../constants/task'
import { showErrorToast, showSuccessToast } from '../common/Toast'

interface Props {
  task: Task
  projectId: string
  onTaskClick?: (taskId: string) => void
}

interface TasksResponse {
  items: Task[]
  total: number
}

/**
 * Cross-task dependency editor (Sprint 1 / S1-7).
 *
 * Shows two lists:
 *  - Blocks: tasks this one is blocking (source.blocks)
 *  - Blocked by: tasks blocking this one (source.blocked_by, read-only)
 *
 * The reverse side is maintained by the server automatically, so this
 * component only mutates ``blocks`` through ``POST/DELETE /tasks/{id}/links``.
 */
export default function TaskLinksSection({ task, projectId, onTaskClick }: Props) {
  const qc = useQueryClient()
  const [showPicker, setShowPicker] = useState(false)
  const [pickerQuery, setPickerQuery] = useState('')
  const pickerInputRef = useRef<HTMLInputElement>(null)

  // Project tasks used to (1) resolve IDs into titles and (2) populate the picker.
  // This query is likely already in-cache via TaskBoard/TaskList so the fetch
  // is effectively free.
  const { data: allTasks = [] } = useQuery<Task[]>({
    queryKey: ['tasks', projectId, 'for-links'],
    queryFn: () =>
      api
        .get<TasksResponse>(`/projects/${projectId}/tasks`, { params: { limit: 500 } })
        .then((r) => r.data.items),
    enabled: !!projectId,
    staleTime: 30_000,
  })

  const taskById = useMemo(() => {
    const m = new Map<string, Task>()
    for (const t of allTasks) m.set(t.id, t)
    return m
  }, [allTasks])

  const blockedByTasks = useMemo(
    () => (task.blocked_by ?? []).map((id) => taskById.get(id)).filter((t): t is Task => !!t),
    [task.blocked_by, taskById],
  )
  const blocksTasks = useMemo(
    () => (task.blocks ?? []).map((id) => taskById.get(id)).filter((t): t is Task => !!t),
    [task.blocks, taskById],
  )

  const pickerCandidates = useMemo(() => {
    const exclude = new Set<string>([
      task.id,
      ...(task.blocks ?? []),
      ...(task.blocked_by ?? []),
    ])
    const q = pickerQuery.trim().toLowerCase()
    return allTasks
      .filter((t) => !exclude.has(t.id) && !t.is_deleted)
      .filter((t) => !q || t.title.toLowerCase().includes(q))
      .slice(0, 20)
  }, [allTasks, task, pickerQuery])

  const linkMut = useMutation({
    mutationFn: (targetId: string) =>
      api.post(`/projects/${projectId}/tasks/${task.id}/links`, {
        target_id: targetId,
        relation: 'blocks',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', task.id] })
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
      qc.invalidateQueries({ queryKey: ['tasks', projectId, 'for-links'] })
      setShowPicker(false)
      setPickerQuery('')
      showSuccessToast('依存関係を追加しました')
    },
    onError: (err: unknown) => {
      // The backend responds with structured ``detail`` for known errors
      // (cycle_detected, duplicate_link, etc.); surface a friendlier message.
      if (err instanceof ApiError) {
        const detail = err.data as { detail?: { error?: string; path?: string[] } } | undefined
        const code = detail?.detail?.error
        if (code === 'cycle_detected') {
          const path = detail?.detail?.path ?? []
          const ids = path.map((id) => `#${id.slice(-4)}`).join(' → ')
          showErrorToast(`循環が発生するため追加できません: ${ids}`)
          return
        }
        if (code === 'duplicate_link') {
          showErrorToast('既に同じ依存関係が存在します')
          return
        }
        if (code === 'self_reference') {
          showErrorToast('自分自身には依存を設定できません')
          return
        }
        if (code === 'cross_project') {
          showErrorToast('別プロジェクトのタスクには依存を設定できません')
          return
        }
      }
      showErrorToast('依存関係の追加に失敗しました')
    },
  })

  const unlinkMut = useMutation({
    mutationFn: (targetId: string) =>
      api.delete(`/projects/${projectId}/tasks/${task.id}/links/${targetId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', task.id] })
      qc.invalidateQueries({ queryKey: ['tasks', projectId] })
      qc.invalidateQueries({ queryKey: ['tasks', projectId, 'for-links'] })
      showSuccessToast('依存関係を解除しました')
    },
    onError: () => {
      showErrorToast('依存関係の解除に失敗しました')
    },
  })

  const inputClasses =
    'w-full border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus'

  const renderRow = (t: Task, canRemove: boolean) => (
    <div
      key={t.id}
      className="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 group"
    >
      <span
        className={clsx('w-2 h-2 rounded-full flex-shrink-0', PRIORITY_DOT_COLORS[t.priority])}
      />
      <button
        onClick={() => onTaskClick?.(t.id)}
        className="flex-1 text-left text-sm text-gray-800 dark:text-gray-100 truncate hover:underline"
      >
        {t.title}
      </button>
      <span
        className={clsx(
          'text-xs px-2 py-0.5 rounded-full flex-shrink-0',
          STATUS_COLORS[t.status],
        )}
      >
        {STATUS_LABELS[t.status]}
      </span>
      {canRemove && (
        <button
          onClick={() => unlinkMut.mutate(t.id)}
          disabled={unlinkMut.isPending}
          className="text-gray-300 dark:text-gray-600 hover:text-crimson dark:hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
          aria-label="依存を解除"
          title="依存を解除"
        >
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  )

  return (
    <div className="space-y-4">
      {/* Blocked by (upstream) */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <Lock className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />
          <label className="block text-sm font-medium text-gray-600 dark:text-gray-400">
            待機中 ({blockedByTasks.length})
          </label>
        </div>
        {blockedByTasks.length > 0 ? (
          <div className="space-y-1">{blockedByTasks.map((t) => renderRow(t, false))}</div>
        ) : (
          <p className="text-sm text-gray-400 dark:text-gray-500">
            このタスクをブロックしているタスクはありません
          </p>
        )}
      </div>

      {/* Blocks (downstream, editable) */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="block text-sm font-medium text-gray-600 dark:text-gray-400">
            ブロック中 ({blocksTasks.length})
          </label>
          {!showPicker && (
            <button
              onClick={() => {
                setShowPicker(true)
                setTimeout(() => pickerInputRef.current?.focus(), 0)
              }}
              className="flex items-center gap-1 text-xs text-accent-600 dark:text-accent-400 hover:text-accent-800 dark:hover:text-accent-300 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              依存を追加
            </button>
          )}
        </div>

        {showPicker && (
          <div className="mb-3 border border-gray-200 dark:border-gray-700 rounded-lg p-2 bg-gray-50 dark:bg-gray-800/50">
            <div className="flex gap-2 mb-2">
              <input
                ref={pickerInputRef}
                type="text"
                value={pickerQuery}
                onChange={(e) => setPickerQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') {
                    setShowPicker(false)
                    setPickerQuery('')
                  }
                }}
                className={inputClasses}
                placeholder="タスクを検索..."
              />
              <button
                onClick={() => {
                  setShowPicker(false)
                  setPickerQuery('')
                }}
                className="px-2 py-2 text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 flex-shrink-0"
                aria-label="キャンセル"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="max-h-60 overflow-y-auto space-y-0.5">
              {pickerCandidates.length > 0 ? (
                pickerCandidates.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => linkMut.mutate(t.id)}
                    disabled={linkMut.isPending}
                    className="w-full flex items-center gap-2 px-3 py-1.5 rounded-md hover:bg-accent-50 dark:hover:bg-accent-900/30 text-left disabled:opacity-50"
                  >
                    <span
                      className={clsx(
                        'w-2 h-2 rounded-full flex-shrink-0',
                        PRIORITY_DOT_COLORS[t.priority],
                      )}
                    />
                    <span className="flex-1 text-sm text-gray-800 dark:text-gray-100 truncate">
                      {t.title}
                    </span>
                    <span
                      className={clsx(
                        'text-xs px-2 py-0.5 rounded-full flex-shrink-0',
                        STATUS_COLORS[t.status],
                      )}
                    >
                      {STATUS_LABELS[t.status]}
                    </span>
                  </button>
                ))
              ) : (
                <p className="text-sm text-gray-400 dark:text-gray-500 px-3 py-2">
                  {pickerQuery.trim() ? '該当するタスクがありません' : '候補がありません'}
                </p>
              )}
            </div>
          </div>
        )}

        {blocksTasks.length > 0 ? (
          <div className="space-y-1">{blocksTasks.map((t) => renderRow(t, true))}</div>
        ) : !showPicker ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">
            このタスクがブロックしているタスクはありません
          </p>
        ) : null}
      </div>
    </div>
  )
}
