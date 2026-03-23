import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Send } from 'lucide-react'
import { api } from '../../api/client'
import type { Comment, Task } from '../../types'

interface Props {
  task: Task
  projectId: string
}

export function TaskCommentList({ task }: { task: Task }) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-600 dark:text-gray-400 mb-3">
        コメント ({task.comments?.length ?? 0})
      </label>
      <div className="space-y-3">
        {task.comments?.map((c: Comment) => (
          <div key={c.id} className="bg-gray-50 dark:bg-gray-700 rounded-lg p-3">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium text-gray-700 dark:text-gray-200">{c.author_name}</span>
              <span className="text-xs text-gray-400 dark:text-gray-500">
                {new Date(c.created_at).toLocaleString('ja-JP', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
              </span>
            </div>
            <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{c.content}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

export function TaskCommentInput({ task, projectId }: Props) {
  const qc = useQueryClient()
  const [comment, setComment] = useState('')

  const addComment = useMutation({
    mutationFn: (content: string) =>
      api.post(`/projects/${projectId}/tasks/${task.id}/comments`, { content }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task', task.id] })
      setComment('')
    },
  })

  return (
    <div className="p-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 dark:bg-gray-900/50">
      <div className="flex gap-2">
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="コメントを入力..."
          className="flex-1 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500"
          rows={2}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && comment.trim()) {
              addComment.mutate(comment.trim())
            }
          }}
        />
        <button
          onClick={() => comment.trim() && addComment.mutate(comment.trim())}
          disabled={!comment.trim() || addComment.isPending}
          className="self-end px-3 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-40"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
