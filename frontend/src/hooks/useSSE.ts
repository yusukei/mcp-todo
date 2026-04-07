import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/auth'
import { showInfoToast } from '../components/common/Toast'

interface SSEEvent {
  type: string
  project_id?: string
  data?: Record<string, unknown>
}

function getEventMessage(event: SSEEvent): string | null {
  const title = (event.data?.title as string) ?? ''
  const short = title.length > 30 ? title.slice(0, 30) + '…' : title

  switch (event.type) {
    case 'task.created':
      return `タスクが追加されました: ${short}`
    case 'task.deleted':
      return 'タスクが削除されました'
    case 'task.updated':
      return `タスクが更新されました: ${short}`
    case 'tasks.batch_created':
      return `${event.data?.count ?? ''}件のタスクが一括追加されました`
    case 'tasks.batch_updated':
      return `${event.data?.count ?? ''}件のタスクが一括更新されました`
    case 'comment.added':
      return 'コメントが追加されました'
    case 'comment.deleted':
      return 'コメントが削除されました'
    case 'project.created':
      return `プロジェクトが作成されました: ${(event.data?.name as string) ?? ''}`
    case 'project.updated':
      return 'プロジェクトが更新されました'
    case 'project.deleted':
      return 'プロジェクトが削除されました'
    default:
      return null
  }
}

export function useSSE() {
  const queryClient = useQueryClient()

  useEffect(() => {
    let es: EventSource | null = null
    let retryCount = 0
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    const MAX_RETRIES = 20

    async function connect() {
      // Cookie auth: only attempt if the user is logged in.
      if (!useAuthStore.getState().user) return

      // Fetch a short-lived, single-use ticket instead of passing JWT in URL
      let ticket: string
      try {
        const { data } = await api.post<{ ticket: string }>('/events/ticket')
        ticket = data.ticket
      } catch {
        // If ticket fetch fails, schedule retry
        if (retryCount < MAX_RETRIES) {
          const delay = Math.min(1000 * 2 ** retryCount, 30000)
          retryTimer = setTimeout(() => { void connect() }, delay)
          retryCount++
        }
        return
      }

      const url = `/api/v1/events?ticket=${encodeURIComponent(ticket)}`
      es = new EventSource(url)

      es.onmessage = (e) => {
        retryCount = 0
        try {
          const event: SSEEvent = JSON.parse(e.data)
          if (!event.type || event.type === 'connected') return

          const projectId = event.project_id

          // Task events
          if (event.type.startsWith('task.') || event.type.startsWith('tasks.')) {
            queryClient.invalidateQueries({ queryKey: ['tasks', projectId] })
            queryClient.invalidateQueries({ queryKey: ['project-summary', projectId] })
            if (event.data?.id) {
              queryClient.invalidateQueries({ queryKey: ['task', event.data.id] })
            }
            // batch events may include task_ids
            if (Array.isArray(event.data?.task_ids)) {
              for (const tid of event.data.task_ids as string[]) {
                queryClient.invalidateQueries({ queryKey: ['task', tid] })
              }
            }
          }

          // Comment events
          if (event.type.startsWith('comment.')) {
            queryClient.invalidateQueries({ queryKey: ['tasks', projectId] })
            queryClient.invalidateQueries({ queryKey: ['project-summary', projectId] })
            if (event.data?.task_id) {
              queryClient.invalidateQueries({ queryKey: ['task', event.data.task_id] })
            }
          }

          // Project events
          if (event.type.startsWith('project.')) {
            queryClient.invalidateQueries({ queryKey: ['projects'] })
            queryClient.invalidateQueries({ queryKey: ['admin-projects'] })
            if (projectId) {
              queryClient.invalidateQueries({ queryKey: ['project', projectId] })
            }
          }

          // Show toast notification
          const message = getEventMessage(event)
          if (message) {
            showInfoToast(message)
          }
        } catch {
          // ignore parse errors
        }
      }

      es.onerror = () => {
        es?.close()
        if (retryCount < MAX_RETRIES) {
          const delay = Math.min(1000 * 2 ** retryCount, 30000)
          retryTimer = setTimeout(() => { void connect() }, delay)
          retryCount++
        }
      }
    }

    void connect()

    const handleOnline = () => {
      retryCount = 0
      es?.close()
      void connect()
    }
    window.addEventListener('online', handleOnline)

    return () => {
      es?.close()
      if (retryTimer) clearTimeout(retryTimer)
      window.removeEventListener('online', handleOnline)
    }
  }, [queryClient])
}
