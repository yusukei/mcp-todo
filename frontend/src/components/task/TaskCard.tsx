/**
 * TaskCard — P0-4 設計プロト整合リライト。
 *
 * 設計プロト (`web-ide/project/workbench-parts.jsx:59-117`) の構造:
 *   row1: <priority-dot 5px/> + <task-id mono 10px ink-4/> + <approved/needs_detail インジケータ (右寄せ, 10px)>
 *   row2: <title 13px ink-1 weight:500 line-clamp:2/>
 *   row3 (active_form があるとき): <pulse dot/> + <active_form text mono 11px terra-1>
 *   row4 (タグ): <tag* … +N>
 *   row5 (フッター): <subtasks 🔗N/> <comments 💬N/> <due 🕘 MM/DD/>
 *
 * Phase 4 で過剰に乗せていた「実行許可トグル」「要判断ピル」「待機 N」
 * 「アーカイブボタン」「コピー icon」などは、TaskDetail 側の責任に
 * 集約し、カード上は **状態の表示** に絞る (設計プロト準拠)。
 */
import { CornerDownRight, Lock, ShieldCheck, MessageSquare, Link2, Calendar } from 'lucide-react'
import clsx from 'clsx'
import type { Task } from '../../types'
import { PRIORITY_DOT_COLORS, PRIORITY_LABELS } from '../../constants/task'

interface Props {
  task: Task
  onClick: () => void
  /** 旧 API 互換: card 上のクイックトグルは廃止したが、props 形状は
   *  既存呼び出し元 (TaskBoard / TaskList / TaskCreateModal preview) を
   *  壊さないため維持。受け取って捨てる。 */
  onUpdateFlags?: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
  onArchive?: (taskId: string, archive: boolean) => void
  selectMode?: boolean
  isSelected?: boolean
  onToggleSelect?: () => void
}

/** 24hex の MongoDB ObjectId を `T<6 大文字 hex>` 形式に短縮表示。
 *  設計プロト の T1/T2/T3 と同じ目的の「人が覚えられるラベル」。 */
function shortTaskId(id: string): string {
  if (!id) return ''
  const cleaned = id.replace(/[^0-9a-zA-Z]/g, '')
  return `T${cleaned.slice(-6).toUpperCase()}`
}

function formatShortDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}/${d.getDate()}`
}

export default function TaskCard({
  task,
  onClick,
  selectMode,
  isSelected,
  onToggleSelect,
}: Props) {
  const isOverdue =
    !!task.due_date &&
    new Date(task.due_date) < new Date() &&
    task.status !== 'done' &&
    task.status !== 'cancelled' &&
    task.status !== 'on_hold'
  const blockedByCount = task.blocked_by_count ?? task.blocked_by?.length ?? 0
  const isBlocked =
    blockedByCount > 0 && task.status !== 'done' && task.status !== 'cancelled'
  const priorityLabel = PRIORITY_LABELS[task.priority]
  const isDecision = task.task_type === 'decision'
  const subtaskCount = task.subtask_count ?? 0
  const commentCount = task.comments?.length ?? 0
  const tagsToShow = task.tags?.slice(0, 3) ?? []
  const tagsOverflow = (task.tags?.length ?? 0) - tagsToShow.length

  return (
    <div
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      role="button"
      aria-label={task.title}
      tabIndex={0}
      className={clsx(
        'group/card relative flex flex-col gap-1.5 rounded-[6px] border bg-gray-700 px-3 py-2.5 text-left cursor-pointer transition-colors',
        'border-line-2 hover:border-accent-500',
        isDecision && 'border-l-2 border-l-decision',
        isOverdue && !isDecision && 'border-l-2 border-l-pri-urgent',
        task.archived && 'opacity-60',
        isBlocked && 'opacity-75',
        selectMode && isSelected && 'ring-2 ring-accent-400',
      )}
    >
      {/* row1: priority dot + 短縮 ID + 右側インジケータ */}
      <div className="flex items-center gap-1.5 text-[10px] font-mono text-gray-300">
        {selectMode && (
          <label
            className="mr-0.5 inline-flex cursor-pointer items-center"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              checked={isSelected ?? false}
              onChange={() => onToggleSelect?.()}
              className="h-3 w-3 rounded border-gray-600 text-accent-500 focus:ring-focus"
            />
          </label>
        )}
        <span
          aria-hidden
          className={clsx('inline-block h-[5px] w-[5px] flex-shrink-0 rounded-full', PRIORITY_DOT_COLORS[task.priority])}
          title={`優先度: ${priorityLabel}`}
        />
        {task.parent_task_id && (
          <CornerDownRight className="h-3 w-3 flex-shrink-0 text-gray-300" aria-hidden />
        )}
        <span className="tracking-wider">{shortTaskId(task.id)}</span>

        {/* 右寄せ: needs_detail > approved の優先順 (設計プロト
            workbench-parts.jsx:88-100 と同じ表示優先) */}
        <span className="ml-auto inline-flex items-center gap-2">
          {isBlocked && (
            <span
              className="inline-flex items-center gap-1 text-blocked"
              title={`${blockedByCount} 件のタスクを待機中`}
            >
              <Lock className="h-3 w-3" />
              {blockedByCount}
            </span>
          )}
          {task.needs_detail && (
            <span className="inline-flex items-center gap-1 text-blocked">
              <span aria-hidden className="inline-block h-[5px] w-[5px] rounded-full bg-blocked" />
              詳細要求
            </span>
          )}
          {task.approved && (
            <span className="inline-flex items-center gap-1 text-approved opacity-90">
              <ShieldCheck className="h-3 w-3" />
              許可
            </span>
          )}
        </span>
      </div>

      {/* row2: title (13px / line-clamp-2) */}
      <p className="text-[13px] font-medium leading-snug text-gray-50 line-clamp-2">
        {task.title}
      </p>

      {/* row3: active_form 行 (進行中タスクで「いま何をやっているか」) */}
      {task.active_form && task.status === 'in_progress' && (
        <div className="-mx-1 flex items-center gap-2 rounded bg-accent-500/[0.10] px-2 py-1 font-mono text-[11px] text-accent-300">
          <span aria-hidden className="status-dot in_progress" />
          <span className="truncate">{task.active_form}</span>
        </div>
      )}

      {/* row4: tags (シアン hairline、設計プロト .tag) */}
      {tagsToShow.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {tagsToShow.map((tag: string) => (
            <span
              key={tag}
              className="inline-block rounded-[3px] border border-focus/15 bg-focus/[0.06] px-[7px] py-[1px] font-mono text-[10.5px] text-gray-200"
            >
              {tag}
            </span>
          ))}
          {tagsOverflow > 0 && (
            <span className="font-mono text-[10.5px] text-gray-300">+{tagsOverflow}</span>
          )}
        </div>
      )}

      {/* row5: footer subtasks / comments / due */}
      {(subtaskCount > 0 || commentCount > 0 || task.due_date) && (
        <div className="mt-0.5 flex items-center gap-3 text-[10.5px] text-gray-300">
          {subtaskCount > 0 && (
            <span className="inline-flex items-center gap-1" title={`サブタスク ${subtaskCount}`}>
              <Link2 className="h-3 w-3" />
              {subtaskCount}
            </span>
          )}
          {commentCount > 0 && (
            <span className="inline-flex items-center gap-1" title={`コメント ${commentCount}`}>
              <MessageSquare className="h-3 w-3" />
              {commentCount}
            </span>
          )}
          <span className="ml-auto" />
          {task.due_date && (
            <span
              className={clsx(
                'inline-flex items-center gap-1',
                isOverdue ? 'text-pri-urgent' : 'text-gray-300',
              )}
              title={isOverdue ? `期限超過: ${task.due_date}` : `期限: ${task.due_date}`}
            >
              <Calendar className="h-3 w-3" />
              {formatShortDate(task.due_date)}
            </span>
          )}
        </div>
      )}
    </div>
  )
}
