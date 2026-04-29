import { useState, useCallback, useMemo, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
  type DragStartEvent,
  type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy, arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Plus, Search, Tag, Pencil, Trash2, History, FileText, FileDown, GripVertical, FileQuestion, CheckSquare, Upload } from 'lucide-react'
import { api } from '../../api/client'
import { showErrorToast, showSuccessToast } from '../common/Toast'
import { showConfirm } from '../common/ConfirmDialog'
import MarkdownRenderer from '../common/MarkdownRenderer'
import CopyUrlButton from '../common/CopyUrlButton'
import type { ProjectDocument, DocumentCategory, DocumentVersionSummary } from '../../types'

const CATEGORIES: { value: DocumentCategory; label: string; color: string }[] = [
  { value: 'spec', label: '仕様', color: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' },
  { value: 'design', label: '設計', color: 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300' },
  { value: 'api', label: 'API', color: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' },
  { value: 'guide', label: 'ガイド', color: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300' },
  { value: 'notes', label: 'ノート', color: 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300' },
]

function categoryStyle(cat: DocumentCategory) {
  return CATEGORIES.find((c) => c.value === cat)?.color ?? ''
}

function categoryLabel(cat: DocumentCategory) {
  return CATEGORIES.find((c) => c.value === cat)?.label ?? cat
}

interface DocFormData {
  title: string
  content: string
  tags: string
  category: DocumentCategory
}

const emptyForm: DocFormData = { title: '', content: '', tags: '', category: 'spec' }

export default function ProjectDocumentsTab({ projectId, initialDocumentId, onSelectId }: { projectId: string; initialDocumentId?: string; onSelectId?: (id: string | null) => void }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [filterCategory, setFilterCategory] = useState<string>('')
  const [filterTag, setFilterTag] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(initialDocumentId ?? null)
  // Mobile: track whether detail panel is shown
  const [mobileShowDetail, setMobileShowDetail] = useState(!!initialDocumentId)
  const [selectMode, setSelectMode] = useState(false)

  const selectDocument = useCallback((id: string | null) => {
    setSelectedId(id)
    setMobileShowDetail(!!id)
    if (onSelectId) {
      onSelectId(id)
    } else if (id) {
      navigate(`/projects/${projectId}/documents/${id}`, { replace: true })
    } else {
      navigate(`/projects/${projectId}?view=docs`, { replace: true })
    }
  }, [navigate, projectId, onSelectId])
  const [editing, setEditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<DocFormData>(emptyForm)
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [exporting, setExporting] = useState(false)

  const toggleCheck = useCallback((id: string) => {
    setCheckedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const exitSelectMode = useCallback(() => {
    setSelectMode(false)
    setCheckedIds(new Set())
  }, [])


  const handleExport = useCallback(async (format: 'markdown' | 'pdf') => {
    if (checkedIds.size === 0) return
    setExporting(true)
    try {
      const resp = await api.post(
        `/projects/${projectId}/documents/export`,
        { document_ids: items.filter((d) => checkedIds.has(d.id)).map((d) => d.id), format },
        { responseType: 'blob', timeout: 120000 },
      )
      const blob = new Blob([resp.data])
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const ext = format === 'pdf' ? 'pdf' : 'md'
      a.download = `documents.${ext}`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      showSuccessToast(`${checkedIds.size}件をエクスポートしました`)
      setCheckedIds(new Set())
    } catch {
      showErrorToast('エクスポートに失敗しました')
    } finally {
      setExporting(false)
    }
  }, [checkedIds, projectId])

  const queryParams = new URLSearchParams()
  if (search) queryParams.set('search', search)
  if (filterCategory) queryParams.set('category', filterCategory)
  if (filterTag) queryParams.set('tag', filterTag)
  queryParams.set('limit', '100')

  const { data, isLoading } = useQuery({
    queryKey: ['documents', projectId, search, filterCategory, filterTag],
    queryFn: () => api.get(`/projects/${projectId}/documents/?${queryParams.toString()}`).then((r) => r.data),
  })

  const items: ProjectDocument[] = data?.items ?? []

  const toggleAll = useCallback(() => {
    setCheckedIds((prev) => {
      if (prev.size === items.length) return new Set()
      return new Set(items.map((d) => d.id))
    })
  }, [items])

  // Fetch document directly when accessed via URL (may not be in filtered list)
  const { data: directDoc } = useQuery<ProjectDocument>({
    queryKey: ['document', projectId, selectedId],
    queryFn: () => api.get(`/projects/${projectId}/documents/${selectedId}`).then((r) => r.data),
    enabled: !!selectedId && !items.find((d) => d.id === selectedId),
  })

  const selected = items.find((d) => d.id === selectedId) ?? directDoc ?? null

  const createMutation = useMutation({
    mutationFn: (payload: object) => api.post(`/projects/${projectId}/documents/`, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents', projectId] })
      setCreating(false)
      setForm(emptyForm)
    },
    onError: () => showErrorToast('作成に失敗しました'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...payload }: { id: string; [key: string]: unknown }) =>
      api.patch(`/projects/${projectId}/documents/${id}`, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents', projectId] })
      setEditing(false)
    },
    onError: () => showErrorToast('更新に失敗しました'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/projects/${projectId}/documents/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['documents', projectId] })
      selectDocument(null)
    },
    onError: () => showErrorToast('削除に失敗しました'),
  })

  // ── Markdown import (multi-file upload) ──
  const importInputRef = useRef<HTMLInputElement>(null)

  const importMutation = useMutation({
    mutationFn: async (files: File[]) => {
      const formData = new FormData()
      for (const file of files) {
        formData.append('files', file)
      }
      const resp = await api.post(
        `/projects/${projectId}/documents/import`,
        formData,
        { timeout: 120000 },
      )
      return resp.data
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['documents', projectId] })
      const imported = data?.imported ?? 0
      const skipped = data?.skipped ?? 0
      if (imported > 0 && skipped === 0) {
        showSuccessToast(`${imported}件のドキュメントをインポートしました`)
      } else if (imported > 0 && skipped > 0) {
        showSuccessToast(`${imported}件インポート、${skipped}件スキップ`)
      } else {
        showErrorToast(`インポートに失敗（${skipped}件スキップ）`)
      }
    },
    onError: () => showErrorToast('インポートに失敗しました'),
  })

  const handleImportClick = useCallback(() => {
    importInputRef.current?.click()
  }, [])

  const handleImportChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files
      if (files && files.length > 0) {
        importMutation.mutate(Array.from(files))
      }
      e.target.value = ''
    },
    [importMutation],
  )

  const handleCreate = useCallback(() => {
    const tags = form.tags.split(',').map((t) => t.trim()).filter(Boolean)
    createMutation.mutate({ title: form.title, content: form.content, tags, category: form.category })
  }, [form, createMutation])

  const handleUpdate = useCallback(() => {
    if (!selectedId) return
    const tags = form.tags.split(',').map((t) => t.trim()).filter(Boolean)
    updateMutation.mutate({ id: selectedId, title: form.title, content: form.content, tags, category: form.category })
  }, [form, selectedId, updateMutation])

  const startEdit = useCallback((d: ProjectDocument) => {
    setForm({ title: d.title, content: d.content, tags: d.tags.join(', '), category: d.category })
    setEditing(true)
  }, [])

  const reorderMutation = useMutation({
    mutationFn: (documentIds: string[]) =>
      api.post(`/projects/${projectId}/documents/reorder`, { document_ids: documentIds }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['documents', projectId] }),
    onError: () => showErrorToast('並び替えに失敗しました'),
  })

  const [activeDragDoc, setActiveDragDoc] = useState<ProjectDocument | null>(null)
  const isFiltered = !!(search || filterCategory || filterTag)
  const docIds = useMemo(() => items.map((d) => d.id), [items])

  const dndSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  )

  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const doc = items.find((d) => d.id === event.active.id)
      if (doc) setActiveDragDoc(doc)
    },
    [items],
  )

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveDragDoc(null)
      const { active, over } = event
      if (!over || active.id === over.id) return
      const oldIndex = docIds.indexOf(active.id as string)
      const newIndex = docIds.indexOf(over.id as string)
      if (oldIndex === -1 || newIndex === -1) return
      const reordered = arrayMove(docIds, oldIndex, newIndex)
      reorderMutation.mutate(reordered)
    },
    [docIds, reorderMutation],
  )

  const allTags = [...new Set(items.flatMap((d) => d.tags))].sort()

  // --- Sidebar (left panel) ---
  const sidebar = (
    <div className={`flex flex-col h-full border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 ${mobileShowDetail ? 'hidden md:flex' : 'flex'} w-full md:w-80 lg:w-96 flex-shrink-0`}>
      {/* Header */}
      <div className="p-3 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-bold text-gray-700 dark:text-gray-200">ドキュメント</h2>
          <div className="flex items-center gap-1">
            <button
              onClick={() => selectMode ? exitSelectMode() : setSelectMode(true)}
              className={`p-1.5 rounded-md transition-colors ${selectMode ? 'bg-accent-100 dark:bg-accent-900/50 text-accent-600 dark:text-accent-400' : 'text-gray-400 dark:text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-700 hover:text-gray-600 dark:hover:text-gray-300'}`}
              title={selectMode ? '選択モード終了' : 'エクスポート用に選択'}
            >
              <CheckSquare className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={handleImportClick}
              disabled={importMutation.isPending}
              className="flex items-center gap-1 px-2 py-1 text-xs border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-300 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
              title="Markdown ファイルをインポート"
            >
              <Upload className="w-3.5 h-3.5" /> {importMutation.isPending ? '...' : 'インポート'}
            </button>
            <input
              ref={importInputRef}
              type="file"
              accept=".md,.markdown,text/markdown"
              multiple
              onChange={handleImportChange}
              className="hidden"
            />
            <button
              onClick={() => { setCreating(true); setMobileShowDetail(true) }}
              className="flex items-center gap-1 px-2 py-1 text-xs bg-accent-500 text-gray-100 rounded-md hover:bg-accent-600"
            >
              <Plus className="w-3.5 h-3.5" /> 追加
            </button>
          </div>
        </div>

        {/* Export bar */}
        {selectMode && (
          <div className="flex items-center gap-2 mb-2 p-2 bg-accent-50 dark:bg-accent-950/40 border border-accent-200 dark:border-accent-800 rounded-md text-xs">
            <span className="font-medium text-accent-700 dark:text-accent-300">
              {checkedIds.size > 0 ? `${checkedIds.size}件選択` : 'エクスポート'}
            </span>
            <div className="flex items-center gap-1 ml-auto">
              <button
                onClick={() => handleExport('markdown')}
                disabled={exporting || checkedIds.size === 0}
                className="flex items-center gap-1 px-2 py-1 bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-200 rounded hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <FileText className="w-3.5 h-3.5" /> MD
              </button>
              <button
                onClick={() => handleExport('pdf')}
                disabled={exporting || checkedIds.size === 0}
                className="flex items-center gap-1 px-2 py-1 bg-accent-500 text-gray-100 rounded hover:bg-accent-600 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <FileDown className="w-3.5 h-3.5" /> {exporting ? '...' : 'PDF'}
              </button>
            </div>
          </div>
        )}

        {/* Filters */}
        <div className="flex items-center gap-1 mt-2">
          <div className="relative flex-1 min-w-0">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-400" />
            <input
              type="text"
              placeholder="検索..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-6 pr-2 py-1 text-xs border border-gray-200 dark:border-gray-600 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
            />
          </div>
          <select
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            className="w-20 px-1 py-1 text-xs border border-gray-200 dark:border-gray-600 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
          >
            <option value="">全カテゴリ</option>
            {CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
          {allTags.length > 0 && (
            <select
              value={filterTag}
              onChange={(e) => setFilterTag(e.target.value)}
              className="w-16 px-1 py-1 text-xs border border-gray-200 dark:border-gray-600 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
            >
              <option value="">全タグ</option>
              {allTags.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <p className="p-3 text-xs text-gray-500 dark:text-gray-400">読み込み中...</p>
        ) : items.length === 0 ? (
          <p className="p-3 text-xs text-gray-500 dark:text-gray-400">ドキュメントがありません</p>
        ) : (
          <div className="py-1">
            {selectMode && items.length > 1 && (
              <label className="flex items-center gap-2 px-3 py-1 text-xs text-gray-500 dark:text-gray-400 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={checkedIds.size === items.length}
                  onChange={toggleAll}
                  className="w-3.5 h-3.5 rounded border-gray-300 dark:border-gray-600 text-accent-600 focus:ring-focus"
                />
                すべて選択
              </label>
            )}
            <DndContext
              sensors={dndSensors}
              collisionDetection={closestCenter}
              onDragStart={handleDragStart}
              onDragEnd={handleDragEnd}
            >
              <SortableContext items={docIds} strategy={verticalListSortingStrategy}>
                {items.map((d) => (
                  <SortableDocumentItem
                    key={d.id}
                    doc={d}
                    projectId={projectId}
                    isSelected={d.id === selectedId}
                    isChecked={checkedIds.has(d.id)}
                    onToggleCheck={toggleCheck}
                    onSelect={selectDocument}
                    sortDisabled={isFiltered}
                    selectMode={selectMode}
                  />
                ))}
              </SortableContext>
              <DragOverlay dropAnimation={null}>
                {activeDragDoc ? (
                  <div className="bg-gray-100 dark:bg-gray-800 shadow-lg rounded-md border border-accent-300 dark:border-accent-600 px-3 py-2 opacity-90 text-sm">
                    {activeDragDoc.title}
                  </div>
                ) : null}
              </DragOverlay>
            </DndContext>
          </div>
        )}
      </div>
    </div>
  )

  // --- Content (right panel) ---
  const content = (
    <div className={`flex-1 flex flex-col min-w-0 h-full ${!mobileShowDetail ? 'hidden md:flex' : 'flex'}`}>
      {creating ? (
        <div className="flex-1 overflow-y-auto p-6">
          <button
            onClick={() => { setCreating(false); setForm(emptyForm); setMobileShowDetail(false) }}
            className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 mb-4 md:hidden"
          >
            &larr; 一覧に戻る
          </button>
          <h2 className="text-xl font-serif font-medium text-gray-800 dark:text-gray-100 mb-6">ドキュメントを追加</h2>
          <DocumentForm
            form={form}
            setForm={setForm}
            onSubmit={handleCreate}
            onCancel={() => { setCreating(false); setForm(emptyForm); setMobileShowDetail(false) }}
            submitLabel="作成"
            loading={createMutation.isPending}
          />
        </div>
      ) : selectedId && selected ? (
        <>
          {/* Sticky header */}
          <div className="border-b border-gray-200 dark:border-gray-700 px-6 py-3 bg-white dark:bg-gray-900 flex items-center justify-between gap-4 flex-shrink-0">
            <div className="flex items-center gap-3 min-w-0">
              <button
                onClick={() => { selectDocument(null); setEditing(false); setMobileShowDetail(false) }}
                className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 flex-shrink-0 md:hidden"
              >
                &larr; 一覧
              </button>
              <h2 className="text-lg font-serif font-medium text-gray-800 dark:text-gray-100 truncate">{selected.title}</h2>
            </div>
            {!editing && (
              <div className="flex items-center gap-2 flex-shrink-0">
                <CopyUrlButton
                  kind="document"
                  contextProjectId={projectId}
                  resourceId={selected.id}
                  title={selected.title}
                  variant="always-visible"
                  size="md"
                />
                <button onClick={() => startEdit(selected)} className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700">
                  <Pencil className="w-4 h-4" />
                </button>
                <button
                  onClick={async () => { if (await showConfirm('削除しますか？')) deleteMutation.mutate(selected.id) }}
                  className="p-2 rounded-lg text-red-500 hover:bg-red-50 dark:hover:bg-red-900/30"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            )}
          </div>

          {editing ? (
            <div className="flex-1 overflow-y-auto p-6">
              <DocumentForm
                form={form}
                setForm={setForm}
                onSubmit={handleUpdate}
                onCancel={() => setEditing(false)}
                submitLabel="更新"
                loading={updateMutation.isPending}
              />
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto p-6">
              <div className="flex flex-wrap items-center gap-2 mb-4">
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${categoryStyle(selected.category)}`}>
                  {categoryLabel(selected.category)}
                </span>
                {selected.tags.map((tag) => (
                  <span key={tag} className="px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                    {tag}
                  </span>
                ))}
              </div>

              <div className="bg-gray-100 dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
                <MarkdownRenderer>{selected.content}</MarkdownRenderer>
              </div>

              <div className="flex items-center justify-between mt-4">
                <p className="text-xs text-gray-400 dark:text-gray-500">
                  v{selected.version} / 更新: {new Date(selected.updated_at).toLocaleString('ja-JP')}
                </p>
              </div>

              <DocumentHistory projectId={projectId} documentId={selected.id} />
            </div>
          )}
        </>
      ) : (
        /* Empty state */
        <div className="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500">
          <div className="text-center">
            <FileQuestion className="w-12 h-12 mx-auto mb-3 opacity-40" />
            <p className="text-sm">ドキュメントを選択してください</p>
          </div>
        </div>
      )}
    </div>
  )

  return (
    <div className="flex h-full">
      {sidebar}
      {content}
    </div>
  )
}


function SortableDocumentItem({
  doc,
  projectId,
  isSelected,
  isChecked,
  onToggleCheck,
  onSelect,
  sortDisabled,
  selectMode,
}: {
  doc: ProjectDocument
  projectId: string
  isSelected: boolean
  isChecked: boolean
  onToggleCheck: (id: string) => void
  onSelect: (id: string) => void
  sortDisabled: boolean
  selectMode: boolean
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: doc.id, disabled: sortDisabled })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`group flex items-center gap-2 px-3 py-2 mx-1 my-0.5 rounded-md cursor-pointer text-sm transition-colors ${
        isDragging ? 'opacity-30' : ''
      } ${
        isSelected
          ? 'bg-accent-100 dark:bg-accent-900/40 text-accent-700 dark:text-accent-300'
          : isChecked
            ? 'bg-accent-50 dark:bg-accent-950/30'
            : 'hover:bg-gray-100 dark:hover:bg-gray-800'
      }`}
    >
      {!sortDisabled && (
        <div
          className="flex-shrink-0 cursor-grab active:cursor-grabbing text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 touch-none"
          {...listeners}
          {...attributes}
        >
          <GripVertical className="w-3.5 h-3.5" />
        </div>
      )}
      {selectMode && (
        <input
          type="checkbox"
          checked={isChecked}
          onChange={() => onToggleCheck(doc.id)}
          onClick={(e) => e.stopPropagation()}
          className="w-3.5 h-3.5 rounded border-gray-300 dark:border-gray-600 text-accent-600 focus:ring-focus flex-shrink-0 cursor-pointer"
        />
      )}
      <button
        onClick={() => onSelect(doc.id)}
        className="text-left flex-1 min-w-0"
      >
        <div className="flex items-center gap-2">
          <span className="truncate font-medium text-gray-800 dark:text-gray-100">{doc.title}</span>
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium flex-shrink-0 ${categoryStyle(doc.category)}`}>
            {categoryLabel(doc.category)}
          </span>
        </div>
        <div className="flex items-center gap-1.5 mt-0.5">
          {doc.tags.slice(0, 2).map((tag) => (
            <span key={tag} className="inline-flex items-center gap-0.5 text-[10px] text-gray-400 dark:text-gray-500">
              <Tag className="w-2.5 h-2.5" />{tag}
            </span>
          ))}
          {doc.tags.length > 2 && (
            <span className="text-[10px] text-gray-400 dark:text-gray-500">+{doc.tags.length - 2}</span>
          )}
          <span className="ml-auto text-[10px] text-gray-400 dark:text-gray-500">
            {new Date(doc.updated_at).toLocaleDateString('ja-JP')}
          </span>
        </div>
      </button>
      <div
        className="flex-shrink-0"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <CopyUrlButton
          kind="document"
          contextProjectId={projectId}
          resourceId={doc.id}
          title={doc.title}
          variant="hover-reveal"
          size="sm"
        />
      </div>
    </div>
  )
}


function DocumentHistory({ projectId, documentId }: { projectId: string; documentId: string }) {
  const [expanded, setExpanded] = useState(false)

  const { data } = useQuery({
    queryKey: ['document-versions', projectId, documentId],
    queryFn: () => api.get(`/projects/${projectId}/documents/${documentId}/versions`).then((r) => r.data),
    enabled: expanded,
  })

  const versions: DocumentVersionSummary[] = data?.items ?? []

  return (
    <div className="mt-4">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
      >
        <History className="w-4 h-4" />
        {expanded ? '履歴を閉じる' : '変更履歴を表示'}
      </button>
      {expanded && (
        <div className="mt-3 space-y-2">
          {versions.length === 0 ? (
            <p className="text-xs text-gray-400 dark:text-gray-500">変更履歴はありません</p>
          ) : (
            versions.map((v) => (
              <div
                key={v.id}
                className="flex items-start gap-3 p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-700"
              >
                <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-medium text-gray-600 dark:text-gray-300">
                  v{v.version}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-700 dark:text-gray-200">{v.title}</span>
                  </div>
                  {v.change_summary && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{v.change_summary}</p>
                  )}
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-xs text-gray-400 dark:text-gray-500">
                      {new Date(v.created_at).toLocaleString('ja-JP')}
                    </span>
                    <span className="text-xs text-gray-400 dark:text-gray-500">by {v.changed_by}</span>
                    {v.task_id && (
                      <span className="text-xs text-accent-500 dark:text-accent-400">
                        task: {v.task_id.slice(-6)}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}


function DocumentForm({
  form, setForm, onSubmit, onCancel, submitLabel, loading,
}: {
  form: DocFormData
  setForm: (f: DocFormData) => void
  onSubmit: () => void
  onCancel: () => void
  submitLabel: string
  loading: boolean
}) {
  return (
    <div className="space-y-4 max-w-3xl">
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">タイトル</label>
        <input
          type="text"
          value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })}
          maxLength={255}
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">内容（Markdown）</label>
        <textarea
          value={form.content}
          onChange={(e) => setForm({ ...form, content: e.target.value })}
          rows={16}
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus font-mono"
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">カテゴリ</label>
          <select
            value={form.category}
            onChange={(e) => setForm({ ...form, category: e.target.value as DocumentCategory })}
            className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
          >
            {CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">タグ（カンマ区切り）</label>
          <input
            type="text"
            value={form.tags}
            onChange={(e) => setForm({ ...form, tags: e.target.value })}
            placeholder="auth, oauth, security"
            className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
          />
        </div>
      </div>
      <div className="flex gap-2 pt-2">
        <button
          onClick={onSubmit}
          disabled={!form.title.trim() || loading}
          className="px-4 py-2 text-sm bg-accent-500 text-gray-100 rounded-lg hover:bg-accent-600 disabled:opacity-50"
        >
          {loading ? '処理中...' : submitLabel}
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
        >
          キャンセル
        </button>
      </div>
    </div>
  )
}
