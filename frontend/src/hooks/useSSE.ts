import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

export function useSSE() {
  const queryClient = useQueryClient()

  useEffect(() => {
    const token = localStorage.getItem('access_token')
    if (!token) return

    const url = `/api/v1/events?token=${encodeURIComponent(token)}`
    const es = new EventSource(url)

    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data)
        if (!event.type || event.type === 'connected') return

        const projectId = event.project_id
        // タスク系イベントでキャッシュを無効化
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
      es.close()
    }

    return () => es.close()
  }, [queryClient])
}
