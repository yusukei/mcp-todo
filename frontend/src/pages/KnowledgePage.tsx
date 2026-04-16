import { useState, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Plus, Search, Tag, Pencil, Trash2, ExternalLink, Copy, Check, FileQuestion, Upload } from 'lucide-react'
import { api } from '../api/client'
import { showConfirm } from '../components/common/ConfirmDialog'
import { showErrorToast, showSuccessToast } from '../components/common/Toast'
import MarkdownRenderer from '../components/common/MarkdownRenderer'
import type { Knowledge, KnowledgeCategory } from '../types'

const CATEGORIES: { value: KnowledgeCategory; label: string; color: string }[] = [
  { value: 'recipe', label: 'レシピ', color: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300' },
  { value: 'reference', label: 'リファレンス', color: 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300' },
  { value: 'tip', label: 'Tips', color: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300' },
  { value: 'troubleshooting', label: 'トラブルシューティング', color: 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300' },
  { value: 'architecture', label: 'アーキテクチャ', color: 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300' },
]

function categoryStyle(cat: KnowledgeCategory) {
  return CATEGORIES.find((c) => c.value === cat)?.color ?? ''
}

function categoryLabel(cat: KnowledgeCategory) {
  return CATEGORIES.find((c) => c.value === cat)?.label ?? cat
}

interface KnowledgeFormData {
  title: string
  content: string
  tags: string
  category: KnowledgeCategory
  source: string
}

const emptyForm: KnowledgeFormData = { title: '', content: '', tags: '', category: 'reference', source: '' }

export default function KnowledgePage() {
  const { knowledgeId } = useParams<{ knowledgeId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [filterCategory, setFilterCategory] = useState<string>('')
  const [filterTag, setFilterTag] = useState('')
  const selectedId = knowledgeId ?? null
  const [editing, setEditing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<KnowledgeFormData>(emptyForm)
  const [copied, setCopied] = useState(false)
  // Mobile: track whether detail panel is shown
  const [mobileShowDetail, setMobileShowDetail] = useState(!!knowledgeId)

  const queryParams = new URLSearchParams()
  if (search) queryParams.set('search', search)
  if (filterCategory) queryParams.set('category', filterCategory)
  if (filterTag) queryParams.set('tag', filterTag)
  queryParams.set('limit', '100')

  const { data, isLoading } = useQuery({
    queryKey: ['knowledge', search, filterCategory, filterTag],
    queryFn: () => api.get(`/knowledge/?${queryParams.toString()}`).then((r) => r.data),
  })

  const items: Knowledge[] = data?.items ?? []

  // Fetch individual knowledge entry when accessed by URL (may not be in list)
  const { data: fetchedKnowledge } = useQuery({
    queryKey: ['knowledge', selectedId],
    queryFn: () => api.get(`/knowledge/${selectedId}`).then((r) => r.data),
    enabled: !!selectedId,
  })

  const selected = (selectedId ? items.find((k) => k.id === selectedId) ?? fetchedKnowledge ?? null : null) as Knowledge | null

  const createMutation = useMutation({
    mutationFn: (payload: object) => api.post('/knowledge/', payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge'] })
      setCreating(false)
      setForm(emptyForm)
    },
    onError: () => showErrorToast('作成に失敗しました'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...payload }: { id: string; [key: string]: unknown }) => api.patch(`/knowledge/${id}`, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge'] })
      setEditing(false)
    },
    onError: () => showErrorToast('更新に失敗しました'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/knowledge/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge'] })
      navigate('/knowledge')
      setMobileShowDetail(false)
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
      const resp = await api.post('/knowledge/import', formData, { timeout: 120000 })
      return resp.data
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['knowledge'] })
      const imported = data?.imported ?? 0
      const skipped = data?.skipped ?? 0
      if (imported > 0 && skipped === 0) {
        showSuccessToast(`${imported}件のナレッジをインポートしました`)
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
    createMutation.mutate({
      title: form.title,
      content: form.content,
      tags,
      category: form.category,
      source: form.source || null,
    })
  }, [form, createMutation])

  const handleUpdate = useCallback(() => {
    if (!selectedId) return
    const tags = form.tags.split(',').map((t) => t.trim()).filter(Boolean)
    updateMutation.mutate({
      id: selectedId,
      title: form.title,
      content: form.content,
      tags,
      category: form.category,
      source: form.source || null,
    })
  }, [form, selectedId, updateMutation])

  const startEdit = useCallback((k: Knowledge) => {
    setForm({
      title: k.title,
      content: k.content,
      tags: k.tags.join(', '),
      category: k.category,
      source: k.source ?? '',
    })
    setEditing(true)
  }, [])

  const selectKnowledge = useCallback((id: string) => {
    navigate(`/knowledge/${id}`)
    setMobileShowDetail(true)
    setEditing(false)
  }, [navigate])

  const backToList = useCallback(() => {
    navigate('/knowledge')
    setMobileShowDetail(false)
    setEditing(false)
  }, [navigate])

  const allTags = [...new Set(items.flatMap((k) => k.tags))].sort()

  // --- Sidebar (left panel) ---
  const sidebar = (
    <div className={`flex flex-col h-full border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 ${mobileShowDetail ? 'hidden md:flex' : 'flex'} w-full md:w-80 lg:w-96 flex-shrink-0`}>
      {/* Header */}
      <div className="p-3 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5">
            <BookOpen className="w-4 h-4 text-terracotta-600 dark:text-terracotta-400" />
            <h2 className="text-sm font-bold text-gray-700 dark:text-gray-200">ナレッジベース</h2>
          </div>
          <div className="flex items-center gap-1">
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
              className="flex items-center gap-1 px-2 py-1 text-xs bg-terracotta-500 text-gray-100 rounded-md hover:bg-terracotta-600"
            >
              <Plus className="w-3.5 h-3.5" /> 追加
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="space-y-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              type="text"
              placeholder="検索..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-8 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
            />
          </div>
          <div className="flex gap-2">
            <select
              value={filterCategory}
              onChange={(e) => setFilterCategory(e.target.value)}
              className="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
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
                className="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
              >
                <option value="">全タグ</option>
                {allTags.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            )}
          </div>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <p className="p-3 text-xs text-gray-500 dark:text-gray-400">読み込み中...</p>
        ) : items.length === 0 ? (
          <p className="p-3 text-xs text-gray-500 dark:text-gray-400">ナレッジがありません</p>
        ) : (
          <div className="py-1">
            {items.map((k) => (
              <button
                key={k.id}
                onClick={() => selectKnowledge(k.id)}
                className={`w-full text-left px-3 py-2 mx-1 my-0.5 rounded-md text-sm transition-colors ${
                  k.id === selectedId
                    ? 'bg-terracotta-100 dark:bg-terracotta-900/40 text-terracotta-700 dark:text-terracotta-300'
                    : 'hover:bg-gray-100 dark:hover:bg-gray-800'
                }`}
                style={{ width: 'calc(100% - 0.5rem)' }}
              >
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium text-gray-800 dark:text-gray-100">{k.title}</span>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium flex-shrink-0 ${categoryStyle(k.category)}`}>
                    {categoryLabel(k.category)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  {k.tags.slice(0, 2).map((tag) => (
                    <span key={tag} className="inline-flex items-center gap-0.5 text-[10px] text-gray-400 dark:text-gray-500">
                      <Tag className="w-2.5 h-2.5" />{tag}
                    </span>
                  ))}
                  {k.tags.length > 2 && (
                    <span className="text-[10px] text-gray-400 dark:text-gray-500">+{k.tags.length - 2}</span>
                  )}
                  <span className="ml-auto text-[10px] text-gray-400 dark:text-gray-500">
                    {new Date(k.updated_at).toLocaleDateString('ja-JP')}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )

  // --- Content (right panel) ---
  const content = (
    <div className={`flex-1 flex flex-col min-w-0 h-full ${!mobileShowDetail ? 'hidden md:flex' : 'flex'}`}>
      {creating ? (
        <div className="flex-1 overflow-y-auto p-6 md:p-8">
          <button
            onClick={() => { setCreating(false); setForm(emptyForm); setMobileShowDetail(false) }}
            className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 mb-4 md:hidden"
          >
            &larr; 一覧に戻る
          </button>
          <h1 className="text-2xl font-serif font-medium text-gray-800 dark:text-gray-100 mb-6">ナレッジを追加</h1>
          <KnowledgeForm
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
                onClick={backToList}
                className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 flex-shrink-0 md:hidden"
              >
                &larr; 一覧
              </button>
              <h1 className="text-lg font-serif font-medium text-gray-800 dark:text-gray-100 truncate">{selected.title}</h1>
            </div>
            {!editing && (
              <div className="flex gap-2 flex-shrink-0">
                <button
                  onClick={() => {
                    const text = `# ${selected.title}\n\n${selected.content}`
                    navigator.clipboard.writeText(text)
                    setCopied(true)
                    showSuccessToast('コピーしました')
                    setTimeout(() => setCopied(false), 2000)
                  }}
                  className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                  title="内容をコピー"
                >
                  {copied ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4" />}
                </button>
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
            <div className="flex-1 overflow-y-auto p-6 md:p-8">
              <KnowledgeForm
                form={form}
                setForm={setForm}
                onSubmit={handleUpdate}
                onCancel={() => setEditing(false)}
                submitLabel="更新"
                loading={updateMutation.isPending}
              />
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto p-6 md:p-8">
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

              {selected.source && (
                <div className="flex items-center gap-1 text-sm text-terracotta-600 dark:text-terracotta-400 mb-4">
                  <ExternalLink className="w-3.5 h-3.5" />
                  <span className="truncate">{selected.source}</span>
                </div>
              )}

              <div className="bg-gray-100 dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
                <MarkdownRenderer>{selected.content}</MarkdownRenderer>
              </div>

              <p className="text-xs text-gray-400 dark:text-gray-500 mt-4">
                更新: {new Date(selected.updated_at).toLocaleString('ja-JP')}
              </p>
            </div>
          )}
        </>
      ) : (
        /* Empty state */
        <div className="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500">
          <div className="text-center">
            <FileQuestion className="w-12 h-12 mx-auto mb-3 opacity-40" />
            <p className="text-sm">ナレッジを選択してください</p>
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


function KnowledgeForm({
  form, setForm, onSubmit, onCancel, submitLabel, loading,
}: {
  form: KnowledgeFormData
  setForm: (f: KnowledgeFormData) => void
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
          rows={12}
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus font-mono"
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">カテゴリ</label>
          <select
            value={form.category}
            onChange={(e) => setForm({ ...form, category: e.target.value as KnowledgeCategory })}
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
            placeholder="fastapi, python, mcp"
            className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
          />
        </div>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">出典（URL・ファイルパス等）</label>
        <input
          type="text"
          value={form.source}
          onChange={(e) => setForm({ ...form, source: e.target.value })}
          placeholder="https://example.com/docs"
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-focus"
        />
      </div>
      <div className="flex gap-2 pt-2">
        <button
          onClick={onSubmit}
          disabled={!form.title.trim() || loading}
          className="px-4 py-2 text-sm bg-terracotta-500 text-gray-100 rounded-lg hover:bg-terracotta-600 disabled:opacity-50"
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
