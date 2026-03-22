import { Calendar, User } from 'lucide-react'
import clsx from 'clsx'

const PRIORITY_COLORS: Record<string, string> = {
  urgent: 'bg-red-100 text-red-700',
  high: 'bg-orange-100 text-orange-700',
  medium: 'bg-yellow-100 text-yellow-700',
  low: 'bg-gray-100 text-gray-600',
}

const PRIORITY_LABELS: Record<string, string> = {
  urgent: '緊急',
  high: '高',
  medium: '中',
  low: '低',
}

interface Props {
  task: any
  onClick: () => void
}

export default function TaskCard({ task, onClick }: Props) {
  const isOverdue = task.due_date && new Date(task.due_date) < new Date() && task.status !== 'done'

  return (
    <div
      onClick={onClick}
      className="bg-white rounded-lg border border-gray-200 p-3 cursor-pointer hover:shadow-sm hover:border-indigo-300 transition-all"
    >
      <p className="text-sm font-medium text-gray-800 mb-2 line-clamp-2">{task.title}</p>

      <div className="flex flex-wrap gap-1 mb-2">
        {task.tags?.map((tag: string) => (
          <span key={tag} className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full">
            {tag}
          </span>
        ))}
      </div>

      <div className="flex items-center justify-between mt-2">
        <span className={clsx('text-xs px-2 py-0.5 rounded-full font-medium', PRIORITY_COLORS[task.priority])}>
          {PRIORITY_LABELS[task.priority]}
        </span>
        <div className="flex items-center gap-2">
          {task.due_date && (
            <span className={clsx('flex items-center gap-1 text-xs', isOverdue ? 'text-red-500' : 'text-gray-400')}>
              <Calendar className="w-3 h-3" />
              {new Date(task.due_date).toLocaleDateString('ja-JP', { month: 'short', day: 'numeric' })}
            </span>
          )}
          {task.assignee_id && (
            <span className="flex items-center gap-1 text-xs text-gray-400">
              <User className="w-3 h-3" />
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
