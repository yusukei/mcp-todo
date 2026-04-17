import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/auth'
import { showInfoToast } from '../components/common/Toast'

interface SSEEvent {
  type: string
  project_id?: string
  data?: Record<string, unknown>
  /** ISO 8601 UTC timestamp of the event's publish moment (Sprint 2 / S2-3). */
  server_time?: string
}

/**
 * localStorage key holding the ISO 8601 timestamp of the last SSE event the
 * client successfully processed. Used as the cursor for reconcile when the
 * stream reconnects after a disconnect (S2-3).
 */
const LAST_SERVER_TIME_KEY = 'sse.lastServerTime'

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
    case 'task.completed':
      return `タスクが完了しました: ${short}`
    case 'task.linked':
    case 'task.unlinked':
      // No toast — link/unlink notifications are too noisy; the UI reflects
      // the change directly through TaskCard/TaskLinksSection re-render.
      return null
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

      // Reconcile (S2-3): after reconnect, refresh all cached task/project
      // queries so any updates that happened during the gap are pulled in.
      // We invalidate broadly rather than calling list_tasks(updated_since=...)
      // because TanStack Query's own stale detection + refetch is simpler and
      // already handles per-route fetching. The server-side ``updated_since``
      // filter is still useful for agents and bespoke clients.
      const lastSeen = (() => {
        try { return localStorage.getItem(LAST_SERVER_TIME_KEY) } catch { return null }
      })()
      if (lastSeen) {
        queryClient.invalidateQueries({ queryKey: ['tasks'] })
        queryClient.invalidateQueries({ queryKey: ['task'] })
        queryClient.invalidateQueries({ queryKey: ['projects'] })
      }

      es.onmessage = (e) => {
        retryCount = 0
        try {
          const event: SSEEvent = JSON.parse(e.data)
          if (!event.type || event.type === 'connected') return

          // Save server_time as the reconcile cursor (S2-3).
          if (event.server_time) {
            try {
              localStorage.setItem(LAST_SERVER_TIME_KEY, event.server_time)
            } catch {
              // Safari private mode / quota — non-fatal.
            }
          }

          const projectId = event.project_id

          // Task events
          if (event.type.startsWith('task.') || event.type.startsWith('tasks.')) {
            queryClient.invalidateQueries({ queryKey: ['tasks', projectId] })
            queryClient.invalidateQueries({ queryKey: ['project-summary', projectId] })
            // Refresh cross-project Live Activity panel (S2-8) on any
            // task change — status/active_form updates can add or remove
            // the task from the in-progress feed.
            queryClient.invalidateQueries({ queryKey: ['tasks', 'live'] })
            if (event.data?.id) {
              queryClient.invalidateQueries({ queryKey: ['task', event.data.id] })
            }
            // batch events may include task_ids
            if (Array.isArray(event.data?.task_ids)) {
              for (const tid of event.data.task_ids as string[]) {
                queryClient.invalidateQueries({ queryKey: ['task', tid] })
              }
            }
            // link/unlink carry source_id + target_id — invalidate both ends
            // so the receiving tab updates blocks/blocked_by without refetch.
            if (event.type === 'task.linked' || event.type === 'task.unlinked') {
              const src = event.data?.source_id as string | undefined
              const tgt = event.data?.target_id as string | undefined
              if (src) queryClient.invalidateQueries({ queryKey: ['task', src] })
              if (tgt) queryClient.invalidateQueries({ queryKey: ['task', tgt] })
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
        } catch (err) {
          console.error('Failed to parse SSE event:', err)
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

    // Tab-return recovery (S2-4): when the user flips back to this tab after
    // being away, tear down the existing stream and reconnect. The reconcile
    // on the new connection catches any events missed while hidden.
    const handleVisibility = () => {
      if (!document.hidden) {
        retryCount = 0
        es?.close()
        void connect()
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)

    return () => {
      es?.close()
      if (retryTimer) clearTimeout(retryTimer)
      window.removeEventListener('online', handleOnline)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [queryClient])
}
