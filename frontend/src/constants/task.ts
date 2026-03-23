import type { TaskStatus, TaskPriority, TaskType } from '../types'

export const STATUS_LABELS: Record<TaskStatus, string> = {
  todo: 'TODO',
  in_progress: '進行中',
  on_hold: '保留',
  done: '完了',
  cancelled: 'キャンセル',
}

export const STATUS_COLORS: Record<TaskStatus, string> = {
  todo: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
  in_progress: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400',
  on_hold: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400',
  done: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400',
  cancelled: 'bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400',
}

export const STATUS_BG_COLORS: Record<TaskStatus, string> = {
  todo: 'bg-gray-100',
  in_progress: 'bg-blue-100',
  on_hold: 'bg-amber-100',
  done: 'bg-green-100',
  cancelled: 'bg-red-100',
}

export const PRIORITY_COLORS: Record<TaskPriority, string> = {
  urgent: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400',
  high: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-400',
  medium: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-400',
  low: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
}

export const PRIORITY_DOT_COLORS: Record<TaskPriority, string> = {
  urgent: 'bg-red-500',
  high: 'bg-orange-500',
  medium: 'bg-yellow-500',
  low: 'bg-gray-400',
}

export const PRIORITY_LABELS: Record<TaskPriority, string> = {
  urgent: '緊急',
  high: '高',
  medium: '中',
  low: '低',
}

export const STATUS_OPTIONS = [
  { value: 'todo' as TaskStatus, label: 'TODO' },
  { value: 'in_progress' as TaskStatus, label: '進行中' },
  { value: 'on_hold' as TaskStatus, label: '保留' },
  { value: 'done' as TaskStatus, label: '完了' },
  { value: 'cancelled' as TaskStatus, label: 'キャンセル' },
]

export const PRIORITY_OPTIONS = [
  { value: 'low' as TaskPriority, label: '低' },
  { value: 'medium' as TaskPriority, label: '中' },
  { value: 'high' as TaskPriority, label: '高' },
  { value: 'urgent' as TaskPriority, label: '緊急' },
]

export const BOARD_COLUMNS: { key: TaskStatus; label: string; color: string; colorDark: string }[] = [
  { key: 'todo', label: 'TODO', color: 'bg-gray-100', colorDark: 'dark:bg-gray-700' },
  { key: 'in_progress', label: '進行中', color: 'bg-blue-100', colorDark: 'dark:bg-blue-900/40' },
  { key: 'on_hold', label: '保留', color: 'bg-amber-100', colorDark: 'dark:bg-amber-900/40' },
  { key: 'done', label: '完了', color: 'bg-green-100', colorDark: 'dark:bg-green-900/40' },
  { key: 'cancelled', label: 'キャンセル', color: 'bg-red-100', colorDark: 'dark:bg-red-900/40' },
]

export const TASK_TYPE_OPTIONS = [
  { value: 'action' as TaskType, label: '作業' },
  { value: 'decision' as TaskType, label: '要判断' },
]

export const REVIEW_FLAG_LABELS = {
  needs_detail: '詳細要求',
  approved: '実行許可',
} as const
