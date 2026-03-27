import { useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Plus, Search, Tag, ArrowLeft, Pencil, Trash2, ExternalLink, Copy, Check } from 'lucide-react'
import { api } from '../api/client'
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
    },
    onError: () => showErrorToast('削除に失敗しました'),
  })

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

  const allTags = [...new Set(items.flatMap((k) => k.tags))].sort()

  // Detail view
  if (selectedId && selected) {
    return (
      <div className="flex-1 overflow-y-auto p-6 md:p-8">
        <button
          onClick={() => { navigate('/knowledge'); setEditing(false) }}
          className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 mb-4"
        >
          <ArrowLeft className="w-4 h-4" /> 一覧に戻る
        </button>

        {editing ? (
          <KnowledgeForm
            form={form}
            setForm={setForm}
            onSubmit={handleUpdate}
            onCancel={() => setEditing(false)}
            submitLabel="更新"
            loading={updateMutation.isPending}
          />
        ) : (
          <div>
            <div className="flex items-start justify-between gap-4 mb-4">
              <h1 className="text-2xl font-bold text-gray-800 dark:text-gray-100">{selected.title}</h1>
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
                  onClick={() => { if (confirm('削除しますか？')) deleteMutation.mutate(selected.id) }}
                  className="p-2 rounded-lg text-red-500 hover:bg-red-50 dark:hover:bg-red-900/30"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>

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
              <div className="flex items-center gap-1 text-sm text-indigo-600 dark:text-indigo-400 mb-4">
                <ExternalLink className="w-3.5 h-3.5" />
                <span className="truncate">{selected.source}</span>
              </div>
            )}

            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
              <MarkdownRenderer>{selected.content}</MarkdownRenderer>
            </div>

            <p className="text-xs text-gray-400 dark:text-gray-500 mt-4">
              更新: {new Date(selected.updated_at).toLocaleString('ja-JP')}
            </p>
          </div>
        )}
      </div>
    )
  }

  // Create view
  if (creating) {
    return (
      <div className="flex-1 overflow-y-auto p-6 md:p-8">
        <button
          onClick={() => { setCreating(false); setForm(emptyForm) }}
          className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 mb-4"
        >
          <ArrowLeft className="w-4 h-4" /> 一覧に戻る
        </button>
        <h1 className="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">ナレッジを追加</h1>
        <KnowledgeForm
          form={form}
          setForm={setForm}
          onSubmit={handleCreate}
          onCancel={() => { setCreating(false); setForm(emptyForm) }}
          submitLabel="作成"
          loading={createMutation.isPending}
        />
      </div>
    )
  }

  // List view
  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-2">
          <BookOpen className="w-6 h-6 text-indigo-600 dark:text-indigo-400" />
          <h1 className="text-2xl font-bold text-gray-800 dark:text-gray-100">ナレッジベース</h1>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
        >
          <Plus className="w-4 h-4" /> 追加
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-6">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="検索..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>
        <select
          value={filterCategory}
          onChange={(e) => setFilterCategory(e.target.value)}
          className="px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
            className="px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">全タグ</option>
            {allTags.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        )}
      </div>

      {/* List */}
      {isLoading ? (
        <p className="text-gray-500 dark:text-gray-400">読み込み中...</p>
      ) : items.length === 0 ? (
        <p className="text-gray-500 dark:text-gray-400">ナレッジがありません</p>
      ) : (
        <div className="grid gap-3">
          {items.map((k) => (
            <button
              key={k.id}
              onClick={() => navigate(`/knowledge/${k.id}`)}
              className="text-left bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4 hover:border-indigo-300 dark:hover:border-indigo-600 transition-colors"
            >
              <div className="flex items-start justify-between gap-2">
                <h3 className="font-semibold text-gray-800 dark:text-gray-100">{k.title}</h3>
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0 ${categoryStyle(k.category)}`}>
                  {categoryLabel(k.category)}
                </span>
              </div>
              {k.content && (
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">
                  {k.content.slice(0, 150)}
                </p>
              )}
              <div className="flex items-center gap-2 mt-2">
                {k.tags.map((tag) => (
                  <span key={tag} className="inline-flex items-center gap-0.5 text-xs text-gray-500 dark:text-gray-400">
                    <Tag className="w-3 h-3" />{tag}
                  </span>
                ))}
                <span className="ml-auto text-xs text-gray-400 dark:text-gray-500">
                  {new Date(k.updated_at).toLocaleDateString('ja-JP')}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
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
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">内容（Markdown）</label>
        <textarea
          value={form.content}
          onChange={(e) => setForm({ ...form, content: e.target.value })}
          rows={12}
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
        />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">カテゴリ</label>
          <select
            value={form.category}
            onChange={(e) => setForm({ ...form, category: e.target.value as KnowledgeCategory })}
            className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
            className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
      </div>
      <div className="flex gap-2 pt-2">
        <button
          onClick={onSubmit}
          disabled={!form.title.trim() || loading}
          className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
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
