import { useEffect, useState, useCallback, useSyncExternalStore } from 'react'
import { X, AlertCircle, CheckCircle, Info } from 'lucide-react'

type ToastType = 'error' | 'success' | 'info'

interface ToastMessage {
  id: number
  text: string
  type: ToastType
}

let nextId = 0
let listeners: Array<() => void> = []
let toasts: ToastMessage[] = []

function emitChange() {
  for (const listener of listeners) listener()
}

export function showErrorToast(text: string) {
  toasts = [...toasts, { id: nextId++, text, type: 'error' }]
  emitChange()
}

export function showSuccessToast(text: string) {
  toasts = [...toasts, { id: nextId++, text, type: 'success' }]
  emitChange()
}

export function showInfoToast(text: string) {
  toasts = [...toasts, { id: nextId++, text, type: 'info' }]
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
    const timeout = toast.type === 'error' ? 8000 : 4000
    const timer = setTimeout(() => {
      setVisible(false)
      setTimeout(() => removeToast(toast.id), 200)
    }, timeout)
    return () => clearTimeout(timer)
  }, [toast.id, toast.type])

  const handleDismiss = useCallback(() => {
    setVisible(false)
    setTimeout(() => removeToast(toast.id), 200)
  }, [toast.id])

  const bgColor = toast.type === 'success' ? 'bg-emerald-600' : toast.type === 'info' ? 'bg-terracotta-500' : 'bg-crimson'
  const Icon = toast.type === 'success' ? CheckCircle : toast.type === 'info' ? Info : AlertCircle

  return (
    <div
      className={`flex items-center gap-2 ${bgColor} text-white px-4 py-3 rounded-lg shadow-whisper text-sm max-w-sm transition-all duration-200 ${
        visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'
      }`}
    >
      <Icon className="w-4 h-4 flex-shrink-0" />
      <span className="flex-1">{toast.text}</span>
      <button onClick={handleDismiss} className="flex-shrink-0 hover:opacity-80" aria-label="閉じる">
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
