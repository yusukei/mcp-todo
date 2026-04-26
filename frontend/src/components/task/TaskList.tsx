import React, { useState, useMemo, useCallback, useEffect, type DOMAttributes } from 'react'
import clsx from 'clsx'
import { Archive, ArchiveRestore, Calendar, CornerDownRight, HelpCircle, Copy, FileDown, GripVertical, ShieldCheck, ShieldOff } from 'lucide-react'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragStartEvent,
  type DragEndEvent,
  type DraggableAttributes,
} from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy, arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { showSuccessToast } from '../common/Toast'
import type { Task } from '../../types'
import { STATUS_LABELS, STATUS_COLORS, PRIORITY_DOT_COLORS } from '../../constants/task'

interface TaskRowProps {
  task: Task
  isSubtask: boolean
  isSelected: boolean
  selectMode: boolean
  sortable: boolean
  onTaskClick: (id: string) => void
  onToggleSelect: (id: string) => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
  onArchive: (taskId: string, archive: boolean) => void
}

const SortableTaskRow = React.memo(function SortableTaskRow(props: TaskRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: props.task.id, disabled: !props.sortable })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

  return (
    <div ref={setNodeRef} style={style} className={isDragging ? 'opacity-30 z-10 relative' : ''}>
      <TaskRowInner {...props} dragListeners={props.sortable ? listeners : undefined} dragAttributes={props.sortable ? attributes : undefined} />
    </div>
  )
})

function TaskRowInner({
  task,
  isSubtask,
  isSelected,
  selectMode,
  onTaskClick,
  onToggleSelect,
  onUpdateFlags,
  onArchive,
  dragListeners,
  dragAttributes,
}: TaskRowProps & { dragListeners?: DOMAttributes<HTMLDivElement>; dragAttributes?: DraggableAttributes }) {
  const isOverdue = task.due_date && new Date(task.due_date) < new Date() && task.status !== 'done' && task.status !== 'cancelled' && task.status !== 'on_hold'

  return (
    <div
      onClick={() => onTaskClick(task.id)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onTaskClick(task.id) } }}
      role="button"
      tabIndex={0}
      className={clsx(
        'flex items-center gap-4 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer',
        task.archived && 'opacity-60',
        isSelected && 'bg-accent-50/50 dark:bg-accent-900/20',
        isSubtask && 'pl-10',
      )}
    >
      {dragListeners && !isSubtask && (
        <div
          className="flex-shrink-0 cursor-grab active:cursor-grabbing text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 touch-none"
          onClick={(e) => e.stopPropagation()}
          {...dragListeners}
          {...dragAttributes}
        >
          <GripVertical className="w-4 h-4" />
        </div>
      )}
      {!dragListeners && !isSubtask && <div className="w-4 flex-shrink-0" />}
      {selectMode && (
        <label className="flex items-center flex-shrink-0 cursor-pointer" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={isSelected}
            onChange={() => onToggleSelect(task.id)}
            className="rounded border-gray-300 dark:border-gray-600 text-accent-600 focus:ring-focus w-3.5 h-3.5"
          />
        </label>
      )}
      <button
        onClick={(e) => {
          e.stopPropagation()
          navigator.clipboard.writeText(task.id)
          showSuccessToast('タスクIDをコピーしました')
        }}
        className="text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 p-0.5 rounded transition-colors flex-shrink-0"
        title={`ID: ${task.id}`}
      >
        <Copy className="w-3 h-3" />
      </button>
      {isSubtask && (
        <CornerDownRight className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500 flex-shrink-0 -ml-2" />
      )}
      <span className={clsx('w-2 h-2 rounded-full flex-shrink-0', PRIORITY_DOT_COLORS[task.priority])} />
      <span className="flex-1 text-sm text-gray-800 dark:text-gray-100 font-medium">{task.title}</span>
      <div className="flex items-center gap-1.5 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
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
      </div>
      {task.tags && task.tags.length > 0 && (
        <div className="flex items-center gap-1 flex-shrink-0">
          {task.tags.slice(0, 2).map((tag: string) => (
            <span key={tag} className="text-xs bg-accent-50 dark:bg-accent-900/40 text-accent-600 dark:text-accent-400 px-2 py-0.5 rounded-full">
              {tag}
            </span>
          ))}
        </div>
      )}
      <div className="flex items-center gap-3 flex-shrink-0">
        {task.due_date && (
          <span className={clsx('flex items-center gap-1 text-xs', isOverdue ? 'text-red-500 dark:text-red-400' : 'text-gray-400 dark:text-gray-500')}>
            <Calendar className="w-3 h-3" />
            {new Date(task.due_date).toLocaleDateString('ja-JP', { month: 'short', day: 'numeric' })}
          </span>
        )}
        <span className={clsx('text-xs px-2 py-0.5 rounded-full', STATUS_COLORS[task.status])}>
          {STATUS_LABELS[task.status]}
        </span>
        {(task.archived || task.status === 'done' || task.status === 'cancelled') && (
          <button
            onClick={(e) => {
              e.stopPropagation()
              onArchive(task.id, !task.archived)
            }}
            className={clsx(
              'p-1 rounded transition-colors',
              task.archived
                ? 'text-accent-500 dark:text-accent-400 hover:bg-accent-50 dark:hover:bg-accent-900/30'
                : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700',
            )}
            title={task.archived ? 'アーカイブ解除' : 'アーカイブ'}
          >
            {task.archived ? <ArchiveRestore className="w-4 h-4" /> : <Archive className="w-4 h-4" />}
          </button>
        )}
      </div>
    </div>
  )
}

interface Props {
  tasks: Task[]
  projectId: string
  selectMode: boolean
  onTaskClick: (id: string) => void
  onUpdateFlags: (taskId: string, flags: { needs_detail?: boolean; approved?: boolean }) => void
  onArchive: (taskId: string, archive: boolean) => void
  onBatchUpdateFlags: (taskIds: string[], flags: { needs_detail?: boolean; approved?: boolean }) => void
  onBatchArchive: (taskIds: string[]) => void
  onBatchUnarchive: (taskIds: string[]) => void
  onExport: (taskIds: string[], format: 'markdown' | 'pdf') => void
  onReorder: (taskIds: string[]) => void
  showArchived: boolean
}

export default function TaskList({ tasks, projectId, selectMode, onTaskClick, onUpdateFlags, onArchive, onBatchUpdateFlags, onBatchArchive, onBatchUnarchive, onExport, onReorder, showArchived }: Props) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [activeTask, setActiveTask] = useState<Task | null>(null)

  useEffect(() => {
    if (!selectMode) setSelectedIds(new Set())
  }, [selectMode])

  const allSelected = tasks.length > 0 && selectedIds.size === tasks.length
  const someSelected = selectedIds.size > 0 && selectedIds.size < tasks.length

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(tasks.map((t) => t.id)))
    }
  }

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

  const bulkUpdateFlags = (flags: { needs_detail?: boolean; approved?: boolean }) => {
    onBatchUpdateFlags(Array.from(selectedIds), flags)
    setSelectedIds(new Set())
  }

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 8 },
    }),
  )

  // Build hierarchical list: parent tasks followed by their subtasks
  const orderedTasks = useMemo(() => {
    const subtaskIds = new Set(
      tasks.filter((t) => t.parent_task_id).map((t) => t.id)
    )
    const subtasksByParent = new Map<string, Task[]>()
    for (const t of tasks) {
      if (t.parent_task_id) {
        const existing = subtasksByParent.get(t.parent_task_id) ?? []
        existing.push(t)
        subtasksByParent.set(t.parent_task_id, existing)
      }
    }

    // Top-level tasks sorted by sort_order
    const topLevel = tasks.filter((t) => !subtaskIds.has(t.id)).sort((a, b) => a.sort_order - b.sort_order)

    const result: { task: Task; isSubtask: boolean }[] = []
    for (const t of topLevel) {
      result.push({ task: t, isSubtask: false })
      const children = subtasksByParent.get(t.id)
      if (children) {
        for (const child of children) {
          result.push({ task: child, isSubtask: true })
        }
      }
    }
    return result
  }, [tasks])

  // Top-level task IDs for sortable context
  const topLevelIds = useMemo(
    () => orderedTasks.filter((e) => !e.isSubtask).map((e) => e.task.id),
    [orderedTasks],
  )

  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const task = tasks.find((t) => t.id === event.active.id)
      if (task) setActiveTask(task)
    },
    [tasks],
  )

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveTask(null)
      const { active, over } = event
      if (!over || active.id === over.id) return

      const oldIndex = topLevelIds.indexOf(active.id as string)
      const newIndex = topLevelIds.indexOf(over.id as string)
      if (oldIndex === -1 || newIndex === -1) return

      const reordered = arrayMove(topLevelIds, oldIndex, newIndex)
      onReorder(reordered)
    },
    [topLevelIds, onReorder],
  )

  return (
    <div className="p-6 overflow-y-auto h-full">
      <div className="bg-gray-100 dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 divide-y divide-gray-100 dark:divide-gray-700">
        {/* Header with select-all and bulk actions */}
        {selectMode && tasks.length > 0 && (
          <div className="flex items-center gap-3 px-4 py-2 bg-gray-50 dark:bg-gray-800/80">
            <div className="w-4 flex-shrink-0" /> {/* spacer for drag handle column */}
            <label className="flex items-center cursor-pointer" onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                checked={allSelected}
                ref={(el) => { if (el) el.indeterminate = someSelected }}
                onChange={toggleSelectAll}
                className="rounded border-gray-300 dark:border-gray-600 text-accent-600 focus:ring-focus w-3.5 h-3.5"
              />
            </label>
            <div className="flex items-center gap-2 flex-1">
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {selectedIds.size > 0 ? `${selectedIds.size}件選択` : '一括操作'}
              </span>
              <div className="flex flex-wrap items-center gap-2 ml-2">
                <button
                  onClick={() => bulkUpdateFlags({ approved: true })}
                  disabled={selectedIds.size === 0}
                  className="text-xs px-2 py-1 rounded bg-approved/15 text-approved hover:bg-approved/25 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  実行許可 ON
                </button>
                <button
                  onClick={() => bulkUpdateFlags({ approved: false })}
                  disabled={selectedIds.size === 0}
                  className="text-xs px-2 py-1 rounded bg-gray-700 text-gray-200 hover:bg-gray-600 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  実行許可 OFF
                </button>
                {!showArchived && (
                  <button
                    onClick={() => {
                      onBatchArchive(Array.from(selectedIds))
                      setSelectedIds(new Set())
                    }}
                    disabled={selectedIds.size === 0}
                    className="text-xs px-2 py-1 rounded bg-accent-100 dark:bg-accent-900/40 text-accent-700 dark:text-accent-400 hover:bg-accent-200 dark:hover:bg-accent-900/60 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    アーカイブ
                  </button>
                )}
                {showArchived && (
                  <button
                    onClick={() => {
                      onBatchUnarchive(Array.from(selectedIds))
                      setSelectedIds(new Set())
                    }}
                    disabled={selectedIds.size === 0}
                    className="text-xs px-2 py-1 rounded bg-accent-100 dark:bg-accent-900/40 text-accent-700 dark:text-accent-400 hover:bg-accent-200 dark:hover:bg-accent-900/60 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    アーカイブ解除
                  </button>
                )}
                <span className="w-px h-4 bg-gray-300 dark:bg-gray-600" />
                <button
                  onClick={() => onExport(orderedTasks.map(e => e.task.id).filter(id => selectedIds.has(id)), 'markdown')}
                  disabled={selectedIds.size === 0}
                  className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-gray-700 text-gray-100 hover:bg-gray-600 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <FileDown className="w-3 h-3" />
                  Markdown
                </button>
                <button
                  onClick={() => onExport(orderedTasks.map(e => e.task.id).filter(id => selectedIds.has(id)), 'pdf')}
                  disabled={selectedIds.size === 0}
                  className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-gray-700 text-gray-100 hover:bg-gray-600 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <FileDown className="w-3 h-3" />
                  PDF
                </button>
              </div>
            </div>
          </div>
        )}
        {tasks.length === 0 && (
          <div className="py-16 text-center text-gray-400 dark:text-gray-500">タスクがありません</div>
        )}
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <SortableContext items={topLevelIds} strategy={verticalListSortingStrategy}>
            {orderedTasks.map(({ task, isSubtask }) => (
              <SortableTaskRow
                key={task.id}
                task={task}
                isSubtask={isSubtask}
                isSelected={selectedIds.has(task.id)}
                selectMode={selectMode}
                sortable={!isSubtask}
                onTaskClick={onTaskClick}
                onToggleSelect={toggleSelect}
                onUpdateFlags={onUpdateFlags}
                onArchive={onArchive}
              />
            ))}
          </SortableContext>
          <DragOverlay dropAnimation={null}>
            {activeTask ? (
              <div className="bg-gray-800 shadow-whisper rounded-comfortable border border-accent-500 px-4 py-3 opacity-90">
                <span className="text-sm font-medium text-gray-50">{activeTask.title}</span>
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      </div>
    </div>
  )
}
