import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Bookmark as BookmarkIcon, Plus, Search, Star, Grid3X3, List, RefreshCw,
  ExternalLink, Trash2, Loader2, AlertCircle, CheckCircle2, X, ImageOff,
  GripVertical, Upload, CheckSquare, Square, FolderInput, Tag, StarOff,
} from 'lucide-react'
import { api } from '../../api/client'
import { showErrorToast, showSuccessToast } from '../common/Toast'
import AuthImage from '../common/AuthImage'
import ClipContentRenderer from '../bookmark/ClipContentRenderer'
import BookmarkCreateModal from '../bookmark/BookmarkCreateModal'
import BookmarkCollectionSidebar from '../bookmark/BookmarkCollectionSidebar'
import type { Bookmark, BookmarkCollection } from '../../types'

interface Props {
  projectId: string
  selectedId?: string | null
  onSelectId?: (id: string | null) => void
}

// ── Thumbnail component with AuthImage + fallback ──────────

function Thumbnail({ bm, size }: { bm: Bookmark; size: 'grid' | 'list' }) {
  const [failed, setFailed] = useState(false)
  const thumbUrl = bm.thumbnail_path
    ? `/api/v1/bookmark-assets/${bm.id}/${bm.thumbnail_path}`
    : null

  if (!thumbUrl || failed) {
    return size === 'grid' ? (
      <div className="h-16 bg-gradient-to-br from-gray-100 to-gray-200 dark:from-gray-700 dark:to-gray-600 flex items-center justify-center">
        {failed ? (
          <ImageOff className="w-6 h-6 text-gray-400 dark:text-gray-500" />
        ) : (
          <BookmarkIcon className="w-6 h-6 text-gray-400 dark:text-gray-500" />
        )}
      </div>
    ) : (
      <div className="w-14 h-10 rounded bg-gray-100 dark:bg-gray-700 flex items-center justify-center flex-shrink-0">
        {failed ? (
          <ImageOff className="w-4 h-4 text-gray-400" />
        ) : (
          <BookmarkIcon className="w-4 h-4 text-gray-400" />
        )}
      </div>
    )
  }

  return size === 'grid' ? (
    <div className="h-32 bg-gray-100 dark:bg-gray-700 overflow-hidden">
      <AuthImage
        src={thumbUrl}
        alt=""
        className="w-full h-full object-cover"
        onLoadError={() => setFailed(true)}
      />
    </div>
  ) : (
    <div className="w-14 h-10 flex-shrink-0 overflow-hidden rounded">
      <AuthImage
        src={thumbUrl}
        alt=""
        className="w-full h-full object-cover"
        onLoadError={() => setFailed(true)}
      />
    </div>
  )
}

// ── Resize handle hook ─────────────────────────────────────

const DETAIL_MIN = 300
const DETAIL_MAX = 800
const DETAIL_DEFAULT = 460
const STORAGE_KEY = 'bookmark-detail-width'

function useResizable() {
  const [width, setWidth] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    return saved ? Math.min(Math.max(Number(saved), DETAIL_MIN), DETAIL_MAX) : DETAIL_DEFAULT
  })
  const dragging = useRef(false)
  const startX = useRef(0)
  const startW = useRef(0)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    startX.current = e.clientX
    startW.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [width])

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return
      // Handle moves to the LEFT to make panel wider (panel is on right side)
      const delta = startX.current - e.clientX
      const next = Math.min(Math.max(startW.current + delta, DETAIL_MIN), DETAIL_MAX)
      setWidth(next)
    }
    const onMouseUp = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      setWidth((w) => { localStorage.setItem(STORAGE_KEY, String(w)); return w })
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
  }, [])

  return { width, onMouseDown }
}

// ── Main component ─────────────────────────────────────────

export default function ProjectBookmarksTab({ projectId, selectedId: externalSelectedId, onSelectId }: Props) {
  const qc = useQueryClient()

  const [search, setSearch] = useState('')
  const [filterTag, setFilterTag] = useState('')
  const [filterCollection, setFilterCollection] = useState<string | null>(null)
  const [filterStarred, setFilterStarred] = useState(false)
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid')
  const [showCreate, setShowCreate] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [selectionMode, setSelectionMode] = useState(false)
  const [internalSelectedId, setInternalSelectedId] = useState<string | null>(externalSelectedId ?? null)

  // Use external control if provided, otherwise internal state
  const selectedId = onSelectId ? (externalSelectedId ?? null) : internalSelectedId
  const setSelectedId = useCallback((id: string | null) => {
    if (onSelectId) {
      onSelectId(id)
    } else {
      setInternalSelectedId(id)
    }
  }, [onSelectId])
  const { width: detailWidth, onMouseDown } = useResizable()

  // ── Data fetching ───────────────────────────────────────

  const PAGE_SIZE = 100

  const {
    data: bookmarksPages,
    isLoading,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useInfiniteQuery({
    queryKey: ['bookmarks', projectId, search, filterTag, filterCollection, filterStarred],
    queryFn: ({ pageParam = 0 }) => {
      const params = new URLSearchParams()
      if (search) params.set('search', search)
      if (filterTag) params.set('tag', filterTag)
      if (filterCollection !== null) params.set('collection_id', filterCollection)
      if (filterStarred) params.set('starred', 'true')
      params.set('limit', String(PAGE_SIZE))
      params.set('skip', String(pageParam))
      return api.get(`/projects/${projectId}/bookmarks/?${params.toString()}`).then((r) => r.data)
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((sum, p) => sum + (p.items?.length ?? 0), 0)
      return loaded < lastPage.total ? loaded : undefined
    },
  })

  const bookmarks: Bookmark[] = useMemo(
    () => bookmarksPages?.pages.flatMap((p) => p.items ?? []) ?? [],
    [bookmarksPages],
  )
  const totalBookmarks = bookmarksPages?.pages[0]?.total ?? 0

  // ── Infinite scroll observer ───────────────────────────
  const sentinelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { rootMargin: '200px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const { data: detail } = useQuery({
    queryKey: ['bookmark', selectedId],
    queryFn: () => api.get(`/projects/${projectId}/bookmarks/${selectedId}`).then((r) => r.data),
    enabled: !!selectedId,
  })

  const { data: collectionsData } = useQuery({
    queryKey: ['bookmark-collections', projectId],
    queryFn: () =>
      api.get(`/projects/${projectId}/bookmark-collections/`).then((r) => r.data),
  })

  const collections: BookmarkCollection[] = collectionsData?.items ?? []

  // ── Mutations ───────────────────────────────────────────

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/projects/${projectId}/bookmarks/${id}`),
    onSuccess: () => {
      showSuccessToast('ブックマークを削除しました')
      qc.invalidateQueries({ queryKey: ['bookmarks', projectId] })
      setSelectedId(null)
    },
    onError: () => showErrorToast('削除に失敗しました'),
  })

  const starMutation = useMutation({
    mutationFn: ({ id, starred }: { id: string; starred: boolean }) =>
      api.patch(`/projects/${projectId}/bookmarks/${id}`, { is_starred: starred }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bookmarks', projectId] })
      if (selectedId) qc.invalidateQueries({ queryKey: ['bookmark', selectedId] })
    },
  })

  const fileInputRef = useRef<HTMLInputElement>(null)

  const importMutation = useMutation({
    mutationFn: (file: File) => {
      const formData = new FormData()
      formData.append('file', file)
      return api.post(`/projects/${projectId}/bookmarks/import`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
    },
    onSuccess: (res) => {
      const data = res.data
      showSuccessToast(`${data.imported}件インポートしました（重複${data.skipped_duplicate}件スキップ）`)
      qc.invalidateQueries({ queryKey: ['bookmarks', projectId] })
    },
    onError: () => showErrorToast('インポートに失敗しました'),
  })

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      importMutation.mutate(file)
      e.target.value = ''  // reset for re-upload
    }
  }

  const reclipMutation = useMutation({
    mutationFn: (id: string) =>
      api.post(`/projects/${projectId}/bookmarks/${id}/clip`),
    onSuccess: () => {
      showSuccessToast('再クリップを開始しました')
      qc.invalidateQueries({ queryKey: ['bookmarks', projectId] })
    },
    onError: () => showErrorToast('再クリップに失敗しました'),
  })

  const batchMutation = useMutation({
    mutationFn: (body: { bookmark_ids: string[]; action: string; collection_id?: string; tags?: string[] }) =>
      api.post(`/projects/${projectId}/bookmarks/batch`, body),
    onSuccess: (res, variables) => {
      const count = res.data.affected
      const labels: Record<string, string> = {
        delete: '削除', star: 'スター', unstar: 'スター解除',
        set_collection: 'コレクション変更', add_tags: 'タグ追加', remove_tags: 'タグ削除',
      }
      showSuccessToast(`${count}件を${labels[variables.action] ?? variables.action}しました`)
      qc.invalidateQueries({ queryKey: ['bookmarks', projectId] })
      if (selectedId) qc.invalidateQueries({ queryKey: ['bookmark', selectedId] })
      setSelectedIds(new Set())
      setSelectionMode(false)
    },
    onError: () => showErrorToast('一括操作に失敗しました'),
  })

  // ── Selection helpers ──────────────────────────────────

  function toggleSelect(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  function toggleSelectAll() {
    if (selectedIds.size === bookmarks.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(bookmarks.map((b) => b.id)))
    }
  }

  function exitSelectionMode() {
    setSelectionMode(false)
    setSelectedIds(new Set())
  }

  // ── Drag & Drop ────────────────────────────────────────

  function handleDragStart(e: React.DragEvent, bmId: string) {
    // If dragging a selected item, drag all selected; otherwise just the one
    const ids = selectionMode && selectedIds.has(bmId) ? [...selectedIds] : [bmId]
    e.dataTransfer.setData('application/x-bookmark-ids', JSON.stringify(ids))
    e.dataTransfer.effectAllowed = 'move'
  }

  function handleDropToCollection(bookmarkIds: string[], collectionId: string) {
    batchMutation.mutate({
      bookmark_ids: bookmarkIds,
      action: 'set_collection',
      collection_id: collectionId,
    })
  }

  // ── Reorder D&D (list within same view) ────────────────
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null)
  const dragSourceIndex = useRef<number | null>(null)

  const reorderMutation = useMutation({
    mutationFn: (ids: string[]) =>
      api.post(`/projects/${projectId}/bookmarks/reorder`, { ids }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bookmarks', projectId] })
    },
    onError: () => showErrorToast('並び替えに失敗しました'),
  })

  function handleReorderDragStart(e: React.DragEvent, index: number, bmId: string) {
    dragSourceIndex.current = index
    e.dataTransfer.setData('application/x-bookmark-reorder', bmId)
    e.dataTransfer.setData('application/x-bookmark-ids', JSON.stringify([bmId]))
    e.dataTransfer.effectAllowed = 'move'
  }

  function handleReorderDragOver(e: React.DragEvent, index: number) {
    if (e.dataTransfer.types.includes('application/x-bookmark-reorder')) {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      setDragOverIndex(index)
    }
  }

  function handleReorderDrop(e: React.DragEvent, dropIndex: number) {
    e.preventDefault()
    setDragOverIndex(null)
    const srcIdx = dragSourceIndex.current
    dragSourceIndex.current = null
    if (srcIdx === null || srcIdx === dropIndex) return

    const reordered = [...bookmarks.map((b) => b.id)]
    const [moved] = reordered.splice(srcIdx, 1)
    reordered.splice(dropIndex, 0, moved)
    reorderMutation.mutate(reordered)
  }

  function handleReorderDragEnd() {
    setDragOverIndex(null)
    dragSourceIndex.current = null
  }

  // ── Helpers ─────────────────────────────────────────────

  function clipStatusIcon(status: string) {
    switch (status) {
      case 'pending':
      case 'processing':
        return <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />
      case 'done':
        return <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
      case 'failed':
        return <AlertCircle className="w-3.5 h-3.5 text-red-500" />
      default:
        return null
    }
  }

  function domainFromUrl(url: string) {
    try { return new URL(url).hostname } catch { return url }
  }

  const selected = detail as Bookmark | undefined

  // ── Render ──────────────────────────────────────────────

  return (
    <div className="flex h-full overflow-hidden">
      {/* Collection sidebar */}
      <BookmarkCollectionSidebar
        projectId={projectId}
        collections={collections}
        selectedCollection={filterCollection}
        onSelectCollection={setFilterCollection}
        starred={filterStarred}
        onToggleStarred={() => setFilterStarred((s) => !s)}
        onDropBookmarks={handleDropToCollection}
      />

      {/* Main list */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <BookmarkIcon className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
              <h2 className="text-base font-bold text-gray-800 dark:text-gray-100">ブックマーク</h2>
              <span className="text-xs text-gray-400">{totalBookmarks}</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => selectionMode ? exitSelectionMode() : setSelectionMode(true)}
                className={`p-1.5 rounded-lg ${selectionMode ? 'text-indigo-600 bg-indigo-100 dark:bg-indigo-900 dark:text-indigo-400' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'}`}
                title={selectionMode ? '選択モード解除' : '選択モード'}
              >
                <CheckSquare className="w-4 h-4" />
              </button>
              <button
                onClick={() => setViewMode(viewMode === 'grid' ? 'list' : 'grid')}
                className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                title={viewMode === 'grid' ? 'リスト表示' : 'グリッド表示'}
              >
                {viewMode === 'grid' ? <List className="w-4 h-4" /> : <Grid3X3 className="w-4 h-4" />}
              </button>
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={importMutation.isPending}
                className="flex items-center gap-1 px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
                title="CSVインポート"
              >
                {importMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                インポート
              </button>
              <input ref={fileInputRef} type="file" accept=".csv" onChange={handleFileSelect} className="hidden" />
              <button
                onClick={() => setShowCreate(true)}
                className="flex items-center gap-1 px-3 py-1.5 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700"
              >
                <Plus className="w-4 h-4" />
                追加
              </button>
            </div>
          </div>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="ブックマークを検索..."
              className="w-full pl-9 pr-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          {filterTag && (
            <div className="flex items-center gap-1 mt-2">
              <span className="text-xs text-gray-500">タグ:</span>
              <span className="px-2 py-0.5 text-xs rounded-full bg-indigo-100 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 flex items-center gap-1">
                {filterTag}
                <button onClick={() => setFilterTag('')}><X className="w-3 h-3" /></button>
              </span>
            </div>
          )}
          {/* Bulk action toolbar */}
          {selectionMode && (
            <div className="flex items-center gap-2 mt-2 py-1.5">
              <button
                onClick={toggleSelectAll}
                className="flex items-center gap-1 text-xs text-gray-600 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400"
              >
                {selectedIds.size === bookmarks.length && bookmarks.length > 0 ? (
                  <CheckSquare className="w-3.5 h-3.5" />
                ) : (
                  <Square className="w-3.5 h-3.5" />
                )}
                {selectedIds.size > 0 ? `${selectedIds.size}件選択` : '全選択'}
              </button>
              {selectedIds.size > 0 && (
                <>
                  <div className="h-4 w-px bg-gray-300 dark:bg-gray-600" />
                  <button
                    onClick={() => batchMutation.mutate({ bookmark_ids: [...selectedIds], action: 'star' })}
                    className="flex items-center gap-1 text-xs text-gray-600 dark:text-gray-400 hover:text-yellow-500 px-1.5 py-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700"
                    title="スター"
                  >
                    <Star className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => batchMutation.mutate({ bookmark_ids: [...selectedIds], action: 'unstar' })}
                    className="flex items-center gap-1 text-xs text-gray-600 dark:text-gray-400 hover:text-gray-500 px-1.5 py-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700"
                    title="スター解除"
                  >
                    <StarOff className="w-3.5 h-3.5" />
                  </button>
                  {collections.length > 0 && (
                    <select
                      onChange={(e) => {
                        if (e.target.value !== '__placeholder__') {
                          batchMutation.mutate({ bookmark_ids: [...selectedIds], action: 'set_collection', collection_id: e.target.value })
                          e.target.value = '__placeholder__'
                        }
                      }}
                      className="text-xs border border-gray-300 dark:border-gray-600 rounded px-1.5 py-0.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300"
                      defaultValue="__placeholder__"
                    >
                      <option value="__placeholder__" disabled>コレクション移動...</option>
                      <option value="">なし (解除)</option>
                      {collections.map((c) => (
                        <option key={c.id} value={c.id}>{c.name}</option>
                      ))}
                    </select>
                  )}
                  <div className="h-4 w-px bg-gray-300 dark:bg-gray-600" />
                  <button
                    onClick={() => {
                      if (confirm(`${selectedIds.size}件のブックマークを削除しますか？`))
                        batchMutation.mutate({ bookmark_ids: [...selectedIds], action: 'delete' })
                    }}
                    className="flex items-center gap-1 text-xs text-red-600 dark:text-red-400 hover:text-red-700 px-1.5 py-0.5 rounded hover:bg-red-50 dark:hover:bg-red-900/30"
                    title="一括削除"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    削除
                  </button>
                  {batchMutation.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin text-indigo-500" />}
                </>
              )}
            </div>
          )}
        </div>

        {/* Bookmark list */}
        <div className="flex-1 overflow-y-auto p-4">
          {isLoading ? (
            <div className="flex items-center justify-center h-32 text-gray-500">読み込み中...</div>
          ) : bookmarks.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-gray-500 dark:text-gray-400">
              <BookmarkIcon className="w-8 h-8 mb-2 opacity-50" />
              <p className="text-sm">ブックマークがありません</p>
              <button
                onClick={() => setShowCreate(true)}
                className="mt-2 text-sm text-indigo-600 dark:text-indigo-400 hover:underline"
              >
                最初のブックマークを追加
              </button>
            </div>
          ) : viewMode === 'grid' ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
              {bookmarks.map((bm) => (
                <div
                  key={bm.id}
                  draggable
                  onDragStart={(e) => handleDragStart(e, bm.id)}
                  onClick={() => selectionMode ? toggleSelect(bm.id) : setSelectedId(bm.id)}
                  className={`cursor-pointer rounded-lg border bg-white dark:bg-gray-800 overflow-hidden hover:shadow-md transition-shadow ${
                    selectionMode && selectedIds.has(bm.id)
                      ? 'border-indigo-500 ring-1 ring-indigo-500 bg-indigo-50 dark:bg-indigo-950'
                      : selectedId === bm.id && !selectionMode
                      ? 'border-indigo-500 ring-1 ring-indigo-500'
                      : 'border-gray-200 dark:border-gray-700'
                  }`}
                >
                  <div className="relative">
                    <Thumbnail bm={bm} size="grid" />
                    {selectionMode && (
                      <div className="absolute top-1.5 left-1.5">
                        {selectedIds.has(bm.id) ? (
                          <CheckSquare className="w-5 h-5 text-indigo-600 dark:text-indigo-400 drop-shadow" />
                        ) : (
                          <Square className="w-5 h-5 text-gray-400 drop-shadow" />
                        )}
                      </div>
                    )}
                  </div>
                  <div className="p-2.5">
                    <div className="flex items-start justify-between gap-1">
                      <h3 className="text-sm font-medium text-gray-800 dark:text-gray-200 line-clamp-2">{bm.title}</h3>
                      <div className="flex items-center gap-1 flex-shrink-0">
                        {clipStatusIcon(bm.clip_status)}
                        {bm.is_starred && <Star className="w-3.5 h-3.5 fill-yellow-400 text-yellow-400" />}
                      </div>
                    </div>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 truncate">{domainFromUrl(bm.url)}</p>
                    {bm.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {bm.tags.slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            onClick={(e) => { e.stopPropagation(); setFilterTag(filterTag === tag ? '' : tag) }}
                            className="px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 cursor-pointer hover:bg-indigo-100 dark:hover:bg-indigo-900"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-1.5">
              {bookmarks.map((bm, idx) => (
                <div
                  key={bm.id}
                  draggable
                  onDragStart={(e) => handleReorderDragStart(e, idx, bm.id)}
                  onDragOver={(e) => handleReorderDragOver(e, idx)}
                  onDrop={(e) => handleReorderDrop(e, idx)}
                  onDragEnd={handleReorderDragEnd}
                  onClick={() => selectionMode ? toggleSelect(bm.id) : setSelectedId(bm.id)}
                  className={`cursor-pointer flex items-center gap-3 px-3 py-2 rounded-lg border bg-white dark:bg-gray-800 hover:shadow-sm transition-all ${
                    dragOverIndex === idx
                      ? 'border-indigo-400 ring-2 ring-indigo-300 dark:ring-indigo-600'
                      : selectionMode && selectedIds.has(bm.id)
                      ? 'border-indigo-500 ring-1 ring-indigo-500 bg-indigo-50 dark:bg-indigo-950'
                      : selectedId === bm.id && !selectionMode
                      ? 'border-indigo-500 ring-1 ring-indigo-500'
                      : 'border-gray-200 dark:border-gray-700'
                  }`}
                >
                  <div className="flex-shrink-0 cursor-grab active:cursor-grabbing text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400">
                    <GripVertical className="w-4 h-4" />
                  </div>
                  {selectionMode && (
                    <div className="flex-shrink-0">
                      {selectedIds.has(bm.id) ? (
                        <CheckSquare className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                      ) : (
                        <Square className="w-4 h-4 text-gray-400" />
                      )}
                    </div>
                  )}
                  <Thumbnail bm={bm} size="list" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">{bm.title}</h3>
                      {clipStatusIcon(bm.clip_status)}
                      {bm.is_starred && <Star className="w-3.5 h-3.5 fill-yellow-400 text-yellow-400" />}
                    </div>
                    <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{domainFromUrl(bm.url)}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
          {/* Infinite scroll sentinel */}
          <div ref={sentinelRef} className="h-1" />
          {isFetchingNextPage && (
            <div className="flex items-center justify-center py-4 text-gray-500">
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
              読み込み中...
            </div>
          )}
        </div>
      </div>

      {/* Detail panel with resize handle */}
      {selectedId && (
        <>
          {/* Resize handle */}
          <div
            onMouseDown={onMouseDown}
            className="hidden md:flex w-1.5 cursor-col-resize items-center justify-center bg-gray-100 dark:bg-gray-700 hover:bg-indigo-200 dark:hover:bg-indigo-800 transition-colors flex-shrink-0 group"
            title="ドラッグしてリサイズ"
          >
            <GripVertical className="w-3 h-3 text-gray-400 group-hover:text-indigo-500" />
          </div>

          {/* Detail content */}
          <div
            className="hidden md:flex flex-col flex-shrink-0 overflow-y-auto bg-white dark:bg-gray-800"
            style={{ width: detailWidth }}
          >
            {!selected ? (
              <div className="flex items-center justify-center h-full text-gray-500">読み込み中...</div>
            ) : (
              <div className="flex flex-col h-full">
                <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-bold text-gray-800 dark:text-gray-100 line-clamp-2 flex-1">{selected.title}</h2>
                    <button onClick={() => setSelectedId(null)} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 ml-2">
                      <X className="w-4 h-4 text-gray-400" />
                    </button>
                  </div>
                  <a
                    href={selected.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline flex items-center gap-1 mt-1"
                  >
                    <ExternalLink className="w-3 h-3" />
                    {domainFromUrl(selected.url)}
                  </a>
                  <div className="flex items-center gap-1.5 mt-2">
                    <button
                      onClick={() => starMutation.mutate({ id: selected.id, starred: !selected.is_starred })}
                      className={`p-1.5 rounded-lg ${selected.is_starred ? 'text-yellow-500' : 'text-gray-400'} hover:bg-gray-100 dark:hover:bg-gray-700`}
                      title={selected.is_starred ? 'スター解除' : 'スターを付ける'}
                    >
                      <Star className={`w-4 h-4 ${selected.is_starred ? 'fill-current' : ''}`} />
                    </button>
                    <button
                      onClick={() => reclipMutation.mutate(selected.id)}
                      className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
                      title="再クリップ"
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => { if (confirm('このブックマークを削除しますか？')) deleteMutation.mutate(selected.id) }}
                      className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-red-500"
                      title="削除"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                  {selected.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {selected.tags.map((tag) => (
                        <span key={tag} className="px-2 py-0.5 text-xs rounded-full bg-indigo-100 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                  {selected.clip_status === 'processing' && (
                    <div className="flex items-center gap-2 mt-2 text-xs text-blue-600 dark:text-blue-400">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />クリップ中...
                    </div>
                  )}
                  {selected.clip_status === 'failed' && (
                    <div className="flex items-center gap-2 mt-2 text-xs text-red-600 dark:text-red-400">
                      <AlertCircle className="w-3.5 h-3.5" />{selected.clip_error}
                    </div>
                  )}
                </div>
                <div className="flex-1 overflow-y-auto px-4 py-4">
                  {selected.description && (
                    <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">{selected.description}</p>
                  )}
                  {selected.clip_content ? (
                    <ClipContentRenderer content={selected.clip_content} />
                  ) : selected.clip_status === 'done' ? (
                    <p className="text-sm text-gray-400">コンテンツを抽出できませんでした</p>
                  ) : null}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* Placeholder when nothing selected */}
      {!selectedId && (
        <div className="hidden md:flex w-0 flex-shrink-0" />
      )}

      {/* Create modal */}
      {showCreate && (
        <BookmarkCreateModal
          projectId={projectId}
          collections={collections}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            qc.invalidateQueries({ queryKey: ['bookmarks', projectId] })
            setShowCreate(false)
          }}
        />
      )}
    </div>
  )
}
