import { useMemo, useState, useCallback, useEffect } from 'react'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  useDroppable,
  closestCenter,
  type DragStartEvent,
  type DragEndEvent,
  type DragOverEvent,
} from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy, arrayMove } from '@dnd-kit/sortable'
import { FileDown } from 'lucide-react'
import TaskCard from './TaskCard'
import SortableTaskCard from './SortableTaskCard'
import type { Task, TaskStatus, TaskPriority } from '../../types'
import { BOARD_COLUMNS } from '../../constants/task'

const PRIORITY_WEIGHT: Record<TaskPriority, number> = {
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
}

interface Props {
  tasks: Task[]
  projectId: string
  onTaskClick: (id: string) => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
  onArchive: (taskId: string, archive: boolean) => void
  onStatusChange: (taskId: string, status: TaskStatus) => void
  onExport: (taskIds: string[], format: 'markdown' | 'pdf') => void
  onReorder: (taskIds: string[]) => void
  showArchived: boolean
  visibleColumns?: TaskStatus[]
  selectMode: boolean
  onExitSelectMode: () => void
}

function DroppableColumn({
  columnKey,
  label,
  color,
  colorDark,
  count,
  isOver,
  taskIds,
  children,
}: {
  columnKey: string
  label: string
  color: string
  colorDark: string
  count: number
  isOver: boolean
  taskIds: string[]
  children: React.ReactNode
}) {
  const { setNodeRef } = useDroppable({ id: columnKey })

  return (
    <div
      ref={setNodeRef}
      className={`flex-1 min-w-[240px] max-w-[600px] flex flex-col rounded-xl transition-all duration-200 ${
        isOver
          ? 'ring-2 ring-accent-400 dark:ring-accent-500 bg-accent-50/50 dark:bg-accent-900/20'
          : ''
      }`}
    >
      <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg mb-2 ${color} ${colorDark}`}>
        <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">{label}</span>
        <span className="text-xs text-gray-500 dark:text-gray-400 bg-white/60 dark:bg-black/20 px-1.5 py-0.5 rounded-full">
          {count}
        </span>
      </div>
      <SortableContext items={taskIds} strategy={verticalListSortingStrategy}>
        <div className="flex-1 space-y-1.5 overflow-y-auto pr-1 min-h-[60px]">
          {children}
        </div>
      </SortableContext>
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
  onExport,
  onReorder,
  showArchived,
  visibleColumns,
  selectMode,
  onExitSelectMode,
}: Props) {
  const columns = visibleColumns
    ? BOARD_COLUMNS.filter((col) => visibleColumns.includes(col.key))
    : BOARD_COLUMNS
  const [activeTask, setActiveTask] = useState<Task | null>(null)
  const [overColumnId, setOverColumnId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())

  const toggleSelect = useCallback((taskId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(taskId)) {
        next.delete(taskId)
      } else {
        next.add(taskId)
      }
      return next
    })
  }, [])

  // Clear selections when exiting select mode
  useEffect(() => {
    if (!selectMode) setSelectedIds(new Set())
  }, [selectMode])

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
  )

  const tasksByStatus = useMemo(() => {
    const map: Record<string, Task[]> = {}
    for (const col of columns) map[col.key] = []
    for (const t of tasks) {
      if (map[t.status]) map[t.status].push(t)
    }
    // Sort by priority first, then by sort_order within same priority
    for (const key of Object.keys(map)) {
      map[key].sort((a, b) => {
        const pw = PRIORITY_WEIGHT[a.priority] - PRIORITY_WEIGHT[b.priority]
        if (pw !== 0) return pw
        return a.sort_order - b.sort_order
      })
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
      // Check if over a task card — find which column it belongs to
      if (overId) {
        const overTask = tasks.find((t) => t.id === overId)
        if (overTask) {
          setOverColumnId(overTask.status)
          return
        }
      }
      setOverColumnId(null)
    }
  }, [columns, tasks])

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveTask(null)
      setOverColumnId(null)

      const { active, over } = event
      if (!over) return

      const taskId = active.id as string
      const overId = over.id as string
      const task = tasks.find((t) => t.id === taskId)
      if (!task) return

      // Check if dropped on a column header (cross-column status change)
      const targetColumn = columns.find((col) => col.key === overId)
      if (targetColumn) {
        if (task.status !== targetColumn.key) {
          onStatusChange(taskId, targetColumn.key)
        }
        return
      }

      // Dropped on another task — check if same column (reorder) or different column
      const overTask = tasks.find((t) => t.id === overId)
      if (!overTask) return

      if (task.status === overTask.status) {
        // Same column: reorder
        const colTasks = tasksByStatus[task.status] ?? []
        const oldIndex = colTasks.findIndex((t) => t.id === taskId)
        const newIndex = colTasks.findIndex((t) => t.id === overId)
        if (oldIndex !== -1 && newIndex !== -1 && oldIndex !== newIndex) {
          const reordered = arrayMove(colTasks, oldIndex, newIndex)
          onReorder(reordered.map((t) => t.id))
        }
      } else {
        // Different column: status change
        onStatusChange(taskId, overTask.status)
      }
    },
    [tasks, onStatusChange, onReorder, columns, tasksByStatus],
  )

  const handleDragCancel = useCallback(() => {
    setActiveTask(null)
    setOverColumnId(null)
  }, [])

  return (
    <div className="relative h-full">
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
        onDragCancel={handleDragCancel}
      >
        <div className={`flex gap-4 px-6 py-4 h-full ${activeTask ? 'overflow-x-hidden' : 'overflow-x-auto'}`}>
          {columns.map((col) => {
            const colTasks = tasksByStatus[col.key] ?? []
            const colTaskIds = colTasks.map((t) => t.id)
            return (
              <DroppableColumn
                key={col.key}
                columnKey={col.key}
                label={col.label}
                color={col.color}
                colorDark={col.colorDark}
                count={colTasks.length}
                isOver={overColumnId === col.key}
                taskIds={colTaskIds}
              >
                {colTasks.map((task) => (
                  selectMode ? (
                    <TaskCard
                      key={task.id}
                      task={task}
                      onClick={() => toggleSelect(task.id)}
                      onUpdateFlags={onUpdateFlags}
                      onArchive={onArchive}
                      selectMode
                      isSelected={selectedIds.has(task.id)}
                      onToggleSelect={() => toggleSelect(task.id)}
                    />
                  ) : (
                    <SortableTaskCard
                      key={task.id}
                      task={task}
                      onClick={() => onTaskClick(task.id)}
                      onUpdateFlags={onUpdateFlags}
                      onArchive={onArchive}
                    />
                  )
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

      {/* Floating export bar */}
      {selectMode && selectedIds.size > 0 && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-3 bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-lg rounded-xl px-4 py-2.5 z-20">
          <span className="text-xs text-gray-500 dark:text-gray-400 font-medium">
            {selectedIds.size}件選択
          </span>
          <button
            onClick={() => onExport(tasks.slice().sort((a, b) => a.sort_order - b.sort_order).map(t => t.id).filter(id => selectedIds.has(id)), 'markdown')}
            className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors font-medium"
          >
            <FileDown className="w-3.5 h-3.5" />
            Markdown
          </button>
          <button
            onClick={() => onExport(tasks.slice().sort((a, b) => a.sort_order - b.sort_order).map(t => t.id).filter(id => selectedIds.has(id)), 'pdf')}
            className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 transition-colors font-medium"
          >
            <FileDown className="w-3.5 h-3.5" />
            PDF
          </button>
        </div>
      )}
    </div>
  )
}
