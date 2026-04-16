import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bookmark, FolderPlus, Star, Inbox, Layers, X, Loader2 } from 'lucide-react'
import { api } from '../../api/client'
import { showErrorToast, showSuccessToast } from '../common/Toast'
import type { BookmarkCollection } from '../../types'

interface Props {
  projectId: string
  collections: BookmarkCollection[]
  selectedCollection: string | null
  onSelectCollection: (id: string | null) => void
  starred: boolean
  onToggleStarred: () => void
  onDropBookmarks?: (bookmarkIds: string[], collectionId: string) => void
}

export default function BookmarkCollectionSidebar({
  projectId,
  collections,
  selectedCollection,
  onSelectCollection,
  starred,
  onToggleStarred,
  onDropBookmarks,
}: Props) {
  const qc = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)
  const [newName, setNewName] = useState('')
  const [dragOverId, setDragOverId] = useState<string | null>(null)

  const createMutation = useMutation({
    mutationFn: (name: string) =>
      api.post(`/projects/${projectId}/bookmark-collections/`, { name }),
    onSuccess: () => {
      showSuccessToast('コレクションを作成しました')
      qc.invalidateQueries({ queryKey: ['bookmark-collections', projectId] })
      setShowAdd(false)
      setNewName('')
    },
    onError: () => showErrorToast('作成に失敗しました'),
  })

  const handleAddSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (newName.trim()) createMutation.mutate(newName.trim())
  }

  const handleDragOver = (e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('application/x-bookmark-ids')) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
    }
  }

  const handleDrop = (e: React.DragEvent, collectionId: string) => {
    e.preventDefault()
    setDragOverId(null)
    const raw = e.dataTransfer.getData('application/x-bookmark-ids')
    if (!raw || !onDropBookmarks) return
    try {
      const ids: string[] = JSON.parse(raw)
      if (ids.length > 0) onDropBookmarks(ids, collectionId)
    } catch (err) {
      console.error('Failed to parse bookmark drag data:', err)
    }
  }

  const handleDragEnter = (e: React.DragEvent, id: string) => {
    if (e.dataTransfer.types.includes('application/x-bookmark-ids')) {
      e.preventDefault()
      setDragOverId(id)
    }
  }

  const handleDragLeave = (e: React.DragEvent) => {
    // Only clear if leaving the element entirely (not entering a child)
    const related = e.relatedTarget as Node | null
    if (!e.currentTarget.contains(related)) {
      setDragOverId(null)
    }
  }

  const itemClass = (active: boolean, isDragOver = false) =>
    `flex items-center gap-2 px-3 py-2 text-sm rounded-lg cursor-pointer transition-colors ${
      isDragOver
        ? 'bg-terracotta-100 dark:bg-terracotta-800/40 ring-2 ring-terracotta-400 text-terracotta-700 dark:text-terracotta-300'
        : active
        ? 'bg-terracotta-50 dark:bg-terracotta-900/30 text-terracotta-700 dark:text-terracotta-300 font-medium'
        : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700'
    }`

  return (
    <div className="hidden lg:flex w-48 flex-col border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 overflow-y-auto">
      <div className="px-3 py-3 border-b border-gray-200 dark:border-gray-700">
        <p className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">
          コレクション
        </p>
      </div>

      <div className="flex-1 px-2 py-2 space-y-0.5">
        {/* All */}
        <div
          onClick={() => {
            onSelectCollection(null)
            if (starred) onToggleStarred()
          }}
          className={itemClass(selectedCollection === null && !starred)}
        >
          <Layers className="w-4 h-4" />
          すべて
        </div>

        {/* Starred */}
        <div
          onClick={() => {
            onSelectCollection(null)
            if (!starred) onToggleStarred()
            else onToggleStarred()
          }}
          className={itemClass(starred)}
        >
          <Star className="w-4 h-4" />
          スター付き
        </div>

        {/* Unsorted */}
        <div
          onClick={() => {
            onSelectCollection('')
            if (starred) onToggleStarred()
          }}
          onDragOver={handleDragOver}
          onDragEnter={(e) => handleDragEnter(e, '__unsorted__')}
          onDragLeave={handleDragLeave}
          onDrop={(e) => handleDrop(e, '')}
          className={itemClass(selectedCollection === '', dragOverId === '__unsorted__')}
        >
          <Inbox className="w-4 h-4" />
          未分類
        </div>

        {/* Collections */}
        {collections.map((c) => (
          <div
            key={c.id}
            onClick={() => {
              onSelectCollection(c.id)
              if (starred) onToggleStarred()
            }}
            onDragOver={handleDragOver}
            onDragEnter={(e) => handleDragEnter(e, c.id)}
            onDragLeave={handleDragLeave}
            onDrop={(e) => handleDrop(e, c.id)}
            className={itemClass(selectedCollection === c.id, dragOverId === c.id)}
          >
            <span className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: c.color }} />
            <span className="truncate">{c.name}</span>
          </div>
        ))}
      </div>

      {/* Add collection */}
      <div className="px-2 py-2 border-t border-gray-200 dark:border-gray-700">
        {showAdd ? (
          <form onSubmit={handleAddSubmit} className="flex items-center gap-1">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="名前..."
              className="flex-1 px-2 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200"
              autoFocus
            />
            <button
              type="submit"
              disabled={!newName.trim() || createMutation.isPending}
              className="p-1 text-terracotta-600 dark:text-terracotta-400 disabled:opacity-50"
            >
              {createMutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <FolderPlus className="w-3.5 h-3.5" />
              )}
            </button>
            <button
              type="button"
              onClick={() => {
                setShowAdd(false)
                setNewName('')
              }}
              className="p-1 text-gray-400"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </form>
        ) : (
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-1 px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:text-terracotta-600 dark:hover:text-terracotta-400 w-full rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            <FolderPlus className="w-3.5 h-3.5" />
            コレクション追加
          </button>
        )}
      </div>
    </div>
  )
}
