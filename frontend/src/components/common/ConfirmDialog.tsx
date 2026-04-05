import { useEffect, useSyncExternalStore } from 'react'
import { AlertTriangle } from 'lucide-react'

interface ConfirmState {
  open: boolean
  message: string
  resolve: ((value: boolean) => void) | null
}

let listeners: Array<() => void> = []
let state: ConfirmState = { open: false, message: '', resolve: null }

function emitChange() {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners = [...listeners, listener]
  return () => {
    listeners = listeners.filter((l) => l !== listener)
  }
}

function getSnapshot() {
  return state
}

export function showConfirm(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    state = { open: true, message, resolve }
    emitChange()
  })
}

function close(result: boolean) {
  state.resolve?.(result)
  state = { open: false, message: '', resolve: null }
  emitChange()
}

export default function ConfirmDialog() {
  const current = useSyncExternalStore(subscribe, getSnapshot)

  useEffect(() => {
    if (!current.open) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(false)
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [current.open])

  if (!current.open) return null

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={() => close(false)} />
      <div className="relative bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-sm w-full mx-4 p-6 animate-in fade-in zoom-in-95 duration-150">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 w-10 h-10 rounded-full bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center">
            <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400" />
          </div>
          <div className="flex-1 pt-1">
            <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{current.message}</p>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-6">
          <button
            onClick={() => close(false)}
            className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg transition-colors"
          >
            キャンセル
          </button>
          <button
            onClick={() => close(true)}
            autoFocus
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors"
          >
            実行
          </button>
        </div>
      </div>
    </div>
  )
}
