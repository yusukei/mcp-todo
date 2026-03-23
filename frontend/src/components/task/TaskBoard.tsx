import { useMemo, useState, useCallback } from 'react'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  useDroppable,
  type DragStartEvent,
  type DragEndEvent,
  type DragOverEvent,
} from '@dnd-kit/core'
import TaskCard from './TaskCard'
import DraggableTaskCard from './DraggableTaskCard'
import type { Task, TaskStatus } from '../../types'
import { BOARD_COLUMNS } from '../../constants/task'

interface Props {
  tasks: Task[]
  projectId: string
  onTaskClick: (id: string) => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
  onArchive: (taskId: string, archive: boolean) => void
  onStatusChange: (taskId: string, status: TaskStatus) => void
  showArchived: boolean
  visibleColumns?: TaskStatus[]
}

function DroppableColumn({
  columnKey,
  label,
  color,
  colorDark,
  count,
  isOver,
  children,
}: {
  columnKey: string
  label: string
  color: string
  colorDark: string
  count: number
  isOver: boolean
  children: React.ReactNode
}) {
  const { setNodeRef } = useDroppable({ id: columnKey })

  return (
    <div
      ref={setNodeRef}
      className={`flex-1 min-w-[240px] max-w-[600px] flex flex-col rounded-xl transition-all duration-200 ${
        isOver
          ? 'ring-2 ring-indigo-400 dark:ring-indigo-500 bg-indigo-50/50 dark:bg-indigo-900/20'
          : ''
      }`}
    >
      <div className={`flex items-center gap-2 px-3 py-2 rounded-lg mb-3 ${color} ${colorDark}`}>
        <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">{label}</span>
        <span className="text-xs text-gray-500 dark:text-gray-400 bg-white/60 dark:bg-black/20 px-1.5 py-0.5 rounded-full">
          {count}
        </span>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto pr-1 min-h-[60px]">
        {children}
      </div>
    </div>
  )
}

export default function TaskBoard({
  tasks,
  projectId,
  onTaskClick,
  onUpdateFlags,
  onArchive,
  onStatusChange,
  showArchived,
  visibleColumns,
}: Props) {
  const columns = visibleColumns
    ? BOARD_COLUMNS.filter((col) => visibleColumns.includes(col.key))
    : BOARD_COLUMNS
  const [activeTask, setActiveTask] = useState<Task | null>(null)
  const [overColumnId, setOverColumnId] = useState<string | null>(null)

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
  )

  const priorityOrder: Record<string, number> = { urgent: 0, high: 1, medium: 2, low: 3 }

  const tasksByStatus = useMemo(() => {
    const map: Record<string, Task[]> = {}
    for (const col of columns) map[col.key] = []
    for (const t of tasks) {
      if (map[t.status]) map[t.status].push(t)
    }
    for (const key of Object.keys(map)) {
      map[key].sort((a, b) => (priorityOrder[a.priority] ?? 99) - (priorityOrder[b.priority] ?? 99))
    }
    return map
  }, [tasks, columns])

  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const taskId = event.active.id as string
      const task = tasks.find((t) => t.id === taskId)
      if (task) setActiveTask(task)
    },
    [tasks],
  )

  const handleDragOver = useCallback((event: DragOverEvent) => {
    const overId = event.over?.id as string | undefined
    if (overId && columns.some((col) => col.key === overId)) {
      setOverColumnId(overId)
    } else {
      setOverColumnId(null)
    }
  }, [columns])

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveTask(null)
      setOverColumnId(null)

      const { active, over } = event
      if (!over) return

      const taskId = active.id as string
      const newStatus = over.id as TaskStatus

      if (!columns.some((col) => col.key === newStatus)) return

      const task = tasks.find((t) => t.id === taskId)
      if (!task || task.status === newStatus) return

      onStatusChange(taskId, newStatus)
    },
    [tasks, onStatusChange, columns],
  )

  const handleDragCancel = useCallback(() => {
    setActiveTask(null)
    setOverColumnId(null)
  }, [])

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      <div className="flex gap-4 p-6 h-full overflow-x-auto">
        {columns.map((col) => {
          const colTasks = tasksByStatus[col.key] ?? []
          return (
            <DroppableColumn
              key={col.key}
              columnKey={col.key}
              label={col.label}
              color={col.color}
              colorDark={col.colorDark}
              count={colTasks.length}
              isOver={overColumnId === col.key}
            >
              {colTasks.map((task) => (
                <DraggableTaskCard
                  key={task.id}
                  task={task}
                  onClick={() => onTaskClick(task.id)}
                  onUpdateFlags={onUpdateFlags}
                  onArchive={onArchive}
                />
              ))}
            </DroppableColumn>
          )
        })}
      </div>

      <DragOverlay dropAnimation={null}>
        {activeTask ? (
          <div className="rotate-2 scale-105 opacity-90">
            <TaskCard
              task={activeTask}
              onClick={() => {}}
              onUpdateFlags={() => {}}
            />
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  )
}
