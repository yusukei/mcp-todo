import TaskCard from './TaskCard'

const COLUMNS = [
  { key: 'todo', label: 'TODO', color: 'bg-gray-100' },
  { key: 'in_progress', label: '進行中', color: 'bg-blue-100' },
  { key: 'in_review', label: 'レビュー中', color: 'bg-yellow-100' },
  { key: 'done', label: '完了', color: 'bg-green-100' },
]

interface Props {
  tasks: any[]
  projectId: string
  onTaskClick: (id: string) => void
}

export default function TaskBoard({ tasks, projectId, onTaskClick }: Props) {
  return (
    <div className="flex gap-4 p-6 h-full overflow-x-auto">
      {COLUMNS.map((col) => {
        const colTasks = tasks.filter((t) => t.status === col.key)
        return (
          <div key={col.key} className="flex-shrink-0 w-72 flex flex-col">
            <div className={`flex items-center gap-2 px-3 py-2 rounded-lg mb-3 ${col.color}`}>
              <span className="text-sm font-semibold text-gray-700">{col.label}</span>
              <span className="text-xs text-gray-500 bg-white/60 px-1.5 py-0.5 rounded-full">
                {colTasks.length}
              </span>
            </div>
            <div className="flex-1 space-y-2 overflow-y-auto pr-1">
              {colTasks.map((task) => (
                <TaskCard key={task.id} task={task} onClick={() => onTaskClick(task.id)} />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
