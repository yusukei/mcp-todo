import clsx from 'clsx'
import { Calendar } from 'lucide-react'
import type { Task } from '../../types'
import { STATUS_LABELS, STATUS_COLORS, PRIORITY_DOT_COLORS } from '../../constants/task'

interface Props {
  tasks: Task[]
  projectId: string
  onTaskClick: (id: string) => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
}

export default function TaskList({ tasks, projectId, onTaskClick, onUpdateFlags }: Props) {
  return (
    <div className="p-6 overflow-y-auto h-full">
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 divide-y divide-gray-100 dark:divide-gray-700">
        {tasks.length === 0 && (
          <div className="py-16 text-center text-gray-400 dark:text-gray-500">タスクがありません</div>
        )}
        {tasks.map((task) => {
          const isOverdue = task.due_date && new Date(task.due_date) < new Date() && task.status !== 'done'
          return (
            <div
              key={task.id}
              onClick={() => onTaskClick(task.id)}
              className="flex items-center gap-4 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer"
            >
              <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', PRIORITY_DOT_COLORS[task.priority])} />
              <span className="flex-1 text-sm text-gray-800 dark:text-gray-100 font-medium">{task.title}</span>
              <div className="flex items-center gap-3 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                <label className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={task.needs_detail}
                    onChange={(e) => onUpdateFlags(task.id, {
                      needs_detail: e.target.checked,
                      ...(e.target.checked ? { approved: false } : {}),
                    })}
                    className="rounded border-amber-300 text-amber-600 focus:ring-amber-500 w-3.5 h-3.5"
                  />
                  詳細要求
                </label>
                <label className="flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-400 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={task.approved}
                    onChange={(e) => onUpdateFlags(task.id, {
                      approved: e.target.checked,
                      ...(e.target.checked ? { needs_detail: false } : {}),
                    })}
                    className="rounded border-emerald-300 text-emerald-600 focus:ring-emerald-500 w-3.5 h-3.5"
                  />
                  実行許可
                </label>
              </div>
              <div className="flex items-center gap-3 flex-shrink-0">
                {task.tags?.slice(0, 2).map((tag: string) => (
                  <span key={tag} className="text-xs bg-indigo-50 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-400 px-2 py-0.5 rounded-full hidden sm:block">
                    {tag}
                  </span>
                ))}
                {task.due_date && (
                  <span className={clsx('flex items-center gap-1 text-xs', isOverdue ? 'text-red-500 dark:text-red-400' : 'text-gray-400 dark:text-gray-500')}>
                    <Calendar className="w-3 h-3" />
                    {new Date(task.due_date).toLocaleDateString('ja-JP', { month: 'short', day: 'numeric' })}
                  </span>
                )}
                <span className={clsx('text-xs px-2 py-0.5 rounded-full', STATUS_COLORS[task.status])}>
                  {STATUS_LABELS[task.status]}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
