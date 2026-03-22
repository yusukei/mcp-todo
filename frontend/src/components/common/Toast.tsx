import { useEffect, useState, useCallback, useSyncExternalStore } from 'react'
import { X, AlertCircle } from 'lucide-react'

interface ToastMessage {
  id: number
  text: string
}

let nextId = 0
let listeners: Array<() => void> = []
let toasts: ToastMessage[] = []

function emitChange() {
  for (const listener of listeners) listener()
}

export function showErrorToast(text: string) {
  toasts = [...toasts, { id: nextId++, text }]
  emitChange()
}

function removeToast(id: number) {
  toasts = toasts.filter((t) => t.id !== id)
  emitChange()
}

function subscribe(listener: () => void) {
  listeners = [...listeners, listener]
  return () => {
    listeners = listeners.filter((l) => l !== listener)
  }
}

function getSnapshot() {
  return toasts
}

function ToastItem({ toast }: { toast: ToastMessage }) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    requestAnimationFrame(() => setVisible(true))
    const timer = setTimeout(() => {
      setVisible(false)
      setTimeout(() => removeToast(toast.id), 200)
    }, 4000)
    return () => clearTimeout(timer)
  }, [toast.id])

  const handleDismiss = useCallback(() => {
    setVisible(false)
    setTimeout(() => removeToast(toast.id), 200)
  }, [toast.id])

  return (
    <div
      className={`flex items-center gap-2 bg-red-600 text-white px-4 py-3 rounded-lg shadow-lg text-sm max-w-sm transition-all duration-200 ${
        visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'
      }`}
    >
      <AlertCircle className="w-4 h-4 flex-shrink-0" />
      <span className="flex-1">{toast.text}</span>
      <button onClick={handleDismiss} className="flex-shrink-0 hover:opacity-80">
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}

export default function ToastContainer() {
  const currentToasts = useSyncExternalStore(subscribe, getSnapshot)

  if (currentToasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2">
      {currentToasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} />
      ))}
    </div>
  )
}
