import { Archive, ArchiveRestore, Calendar, User, CornerDownRight, HelpCircle, Copy, Lock, ShieldCheck, ShieldOff } from 'lucide-react'
import { showSuccessToast } from '../common/Toast'
import clsx from 'clsx'
import type { Task } from '../../types'
import { PRIORITY_DOT_COLORS, PRIORITY_LABELS } from '../../constants/task'

interface Props {
  task: Task
  onClick: () => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
  onArchive?: (taskId: string, archive: boolean) => void
  selectMode?: boolean
  isSelected?: boolean
  onToggleSelect?: () => void
}

export default function TaskCard({ task, onClick, onUpdateFlags, onArchive, selectMode, isSelected, onToggleSelect }: Props) {
  const isOverdue = task.due_date && new Date(task.due_date) < new Date() && task.status !== 'done' && task.status !== 'cancelled' && task.status !== 'on_hold'
  const blockedByCount = task.blocked_by?.length ?? 0
  const isBlocked = blockedByCount > 0 && task.status !== 'done' && task.status !== 'cancelled'
  const priorityLabel = PRIORITY_LABELS[task.priority]

  return (
    <div
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick() } }}
      role="button"
      aria-label={task.title}
      tabIndex={0}
      className={clsx(
        'relative bg-gray-800 rounded-comfortable border border-gray-700 px-3 py-2 cursor-pointer hover:border-accent-500 hover:shadow-whisper transition-all group/card',
        task.archived && 'opacity-60',
        isBlocked && 'opacity-70',
        isOverdue && 'border-l-4 border-l-pri-urgent',
        selectMode && isSelected && 'ring-2 ring-accent-400',
      )}
    >
      <button
        onClick={(e) => {
          e.stopPropagation()
          navigator.clipboard.writeText(task.id)
          showSuccessToast('タスクIDをコピーしました')
        }}
        className="absolute top-1.5 right-1.5 text-gray-400 hover:text-gray-200 p-0.5 rounded transition-all opacity-0 group-hover/card:opacity-100"
        title={`ID: ${task.id}`}
      >
        <Copy className="w-3 h-3" />
      </button>
      {task.parent_task_id && (
        <div className="flex items-center gap-1 mb-1">
          <CornerDownRight className="w-3 h-3 text-gray-300" />
          <span className="text-xs text-gray-300">サブタスク</span>
        </div>
      )}
      <div className="flex items-start gap-2 mb-1.5">
        {selectMode && (
          <label
            className="flex items-center mt-0.5 cursor-pointer"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              checked={isSelected ?? false}
              onChange={() => onToggleSelect?.()}
              className="rounded border-gray-600 text-accent-500 focus:ring-focus w-4 h-4"
            />
          </label>
        )}
        <span
          className={clsx(
            'inline-block w-2 h-2 rounded-full flex-shrink-0 mt-1.5',
            PRIORITY_DOT_COLORS[task.priority],
          )}
          aria-label={`優先度: ${priorityLabel}`}
          title={`優先度: ${priorityLabel}`}
        />
        <p className="text-sm font-medium text-gray-50 line-clamp-2 leading-snug">{task.title}</p>
      </div>

      <div className="flex flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={() => onUpdateFlags(task.id, { approved: !task.approved })}
          className={clsx(
            'inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full border transition-all',
            task.approved
              ? 'bg-approved/15 text-approved border-approved/40 shadow-sm'
              : 'bg-gray-700/50 text-gray-300 border-gray-600 hover:bg-gray-700',
          )}
          aria-label={task.approved ? '実行許可を取消' : '実行許可を付与'}
        >
          {task.approved ? <ShieldCheck className="w-3 h-3" /> : <ShieldOff className="w-3 h-3" />}
          実行許可
        </button>
        {task.task_type === 'decision' && (
          <span className="flex items-center gap-0.5 text-xs px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap bg-decision/15 text-decision">
            <HelpCircle className="w-3 h-3" />
            要判断
          </span>
        )}
        {isBlocked && (
          <span
            className="flex items-center gap-0.5 text-xs px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap bg-blocked/15 text-blocked"
            title={`${blockedByCount}件のタスクを待機中`}
          >
            <Lock className="w-3 h-3" />
            待機 {blockedByCount}
          </span>
        )}
        {task.tags?.map((tag: string) => (
          <span key={tag} className="text-xs bg-accent-900/40 text-accent-300 px-2 py-0.5 rounded-full">
            {tag}
          </span>
        ))}
        {task.due_date && (
          <span className={clsx('flex items-center gap-1 text-xs', isOverdue ? 'text-pri-urgent' : 'text-gray-300')}>
            <Calendar className="w-3 h-3" />
            {new Date(task.due_date).toLocaleDateString('ja-JP', { month: 'short', day: 'numeric' })}
          </span>
        )}
        {task.assignee_id && (
          <span className="flex items-center gap-1 text-xs text-gray-300">
            <User className="w-3 h-3" />
          </span>
        )}
        {onArchive && (task.archived || task.status === 'done' || task.status === 'cancelled') && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onArchive(task.id, !task.archived)
            }}
            className={clsx(
              'ml-auto p-0.5 rounded transition-colors',
              task.archived
                ? 'text-accent-400 hover:bg-accent-900/30'
                : 'text-gray-300 hover:bg-gray-700',
            )}
            title={task.archived ? 'アーカイブ解除' : 'アーカイブ'}
          >
            {task.archived ? <ArchiveRestore className="w-3.5 h-3.5" /> : <Archive className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>
    </div>
  )
}
