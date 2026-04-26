import type { TaskStatus, TaskPriority, TaskType } from '../types'

export const STATUS_LABELS: Record<TaskStatus, string> = {
  todo: 'TODO',
  in_progress: '進行中',
  on_hold: '保留',
  done: '完了',
  cancelled: 'キャンセル',
}

// Phase 4: Monokai Pro semantic tokens. Status colors use the
// `status-*` palette defined in tailwind.config.js (cyan / yellow /
// green / pink). Light-mode is intentionally broken — see UI 再設計
// 仕様書 §6 (Monokai Light is a future epic).
export const STATUS_COLORS: Record<TaskStatus, string> = {
  todo: 'bg-gray-700 text-gray-200',
  in_progress: 'bg-status-progress/15 text-status-progress',
  on_hold: 'bg-status-hold/15 text-status-hold',
  done: 'bg-status-done/15 text-status-done',
  cancelled: 'bg-status-cancel/15 text-status-cancel',
}

// Used for board column headers — slightly stronger surface tint
// than the badges so the column is visually distinct from cards.
export const STATUS_BG_COLORS: Record<TaskStatus, string> = {
  todo: 'bg-gray-800',
  in_progress: 'bg-status-progress/10',
  on_hold: 'bg-status-hold/10',
  done: 'bg-status-done/10',
  cancelled: 'bg-status-cancel/10',
}

// Pill-style badge (kept for callers that still want a labeled
// priority chip — TaskCard now uses dot-only via PRIORITY_DOT_COLORS).
export const PRIORITY_COLORS: Record<TaskPriority, string> = {
  urgent: 'bg-pri-urgent/15 text-pri-urgent',
  high: 'bg-pri-high/15 text-pri-high',
  medium: 'bg-pri-medium/15 text-pri-medium',
  low: 'bg-pri-low/15 text-gray-200',
}

export const PRIORITY_DOT_COLORS: Record<TaskPriority, string> = {
  urgent: 'bg-pri-urgent',
  high: 'bg-pri-high',
  medium: 'bg-pri-medium',
  low: 'bg-pri-low',
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
  { key: 'todo', label: 'TODO', color: 'bg-gray-800', colorDark: 'dark:bg-gray-800' },
  { key: 'in_progress', label: '進行中', color: 'bg-status-progress/10', colorDark: 'dark:bg-status-progress/10' },
  { key: 'on_hold', label: '保留', color: 'bg-status-hold/10', colorDark: 'dark:bg-status-hold/10' },
  { key: 'done', label: '完了', color: 'bg-status-done/10', colorDark: 'dark:bg-status-done/10' },
  { key: 'cancelled', label: 'キャンセル', color: 'bg-status-cancel/10', colorDark: 'dark:bg-status-cancel/10' },
]

export const TASK_TYPE_OPTIONS = [
  { value: 'action' as TaskType, label: '作業' },
  { value: 'decision' as TaskType, label: '要判断' },
]

export const REVIEW_FLAG_LABELS = {
  needs_detail: '詳細要求',
  approved: '実行許可',
} as const
