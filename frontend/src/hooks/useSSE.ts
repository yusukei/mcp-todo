import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

export function useSSE() {
  const queryClient = useQueryClient()

  useEffect(() => {
    let es: EventSource | null = null
    let retryCount = 0
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    const MAX_RETRIES = 20

    function connect() {
      const token = localStorage.getItem('access_token')
      if (!token) return

      const url = `/api/v1/events?token=${encodeURIComponent(token)}`
      es = new EventSource(url)

      es.onmessage = (e) => {
        retryCount = 0
        try {
          const event = JSON.parse(e.data)
          if (!event.type || event.type === 'connected') return

          const projectId = event.project_id
          if (event.type.startsWith('task.') || event.type.startsWith('comment.')) {
            queryClient.invalidateQueries({ queryKey: ['tasks', projectId] })
            queryClient.invalidateQueries({ queryKey: ['task', event.data?.id] })
            queryClient.invalidateQueries({ queryKey: ['project-summary', projectId] })
          }
        } catch {
          // ignore parse errors
        }
      }

      es.onerror = () => {
        es?.close()
        if (retryCount < MAX_RETRIES) {
          const delay = Math.min(1000 * 2 ** retryCount, 30000)
          retryTimer = setTimeout(connect, delay)
          retryCount++
        }
      }
    }

    connect()

    const handleOnline = () => {
      retryCount = 0
      es?.close()
      connect()
    }
    window.addEventListener('online', handleOnline)

    return () => {
      es?.close()
      if (retryTimer) clearTimeout(retryTimer)
      window.removeEventListener('online', handleOnline)
    }
  }, [queryClient])
}
