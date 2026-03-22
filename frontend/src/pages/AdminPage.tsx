import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Plus, Key, Users, Mail, FolderOpen, Copy, Check } from 'lucide-react'
import { api } from '../api/client'
import type { User, AllowedEmail, McpApiKey, Project } from '../types'

type Tab = 'users' | 'emails' | 'keys' | 'projects'

// ────────────────────────────────────────────────────────────
// Users tab
// ────────────────────────────────────────────────────────────
function UsersTab() {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)

  const { data: users = [] } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api.get('/users').then((r) => r.data),
  })

  const create = useMutation({
    mutationFn: () => api.post('/users', { email, name, password, is_admin: isAdmin }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-users'] })
      setEmail(''); setName(''); setPassword(''); setIsAdmin(false); setShowForm(false)
    },
  })

  const toggleActive = useMutation({
    mutationFn: (u: User) => api.patch(`/users/${u.id}`, { is_active: !u.is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
  })

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/users/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-gray-700 dark:text-gray-200">ユーザ管理</h2>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
        >
          <Plus className="w-4 h-4" />ユーザ追加
        </button>
      </div>

      {showForm && (
        <div className="mb-4 p-4 border border-gray-200 dark:border-gray-600 rounded-xl bg-gray-50 dark:bg-gray-700 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <input
              placeholder="メールアドレス"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <input
              placeholder="名前"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <input
              type="password"
              placeholder="パスワード"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
              <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
              管理者権限
            </label>
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowForm(false)} className="px-3 py-1.5 text-sm text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-600">キャンセル</button>
            <button
              onClick={() => create.mutate()}
              disabled={!email || !name || create.isPending}
              className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {create.isPending ? '作成中...' : '作成'}
            </button>
          </div>
        </div>
      )}

      <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400 text-xs uppercase">
            <tr>
              <th className="px-4 py-3 text-left">名前</th>
              <th className="px-4 py-3 text-left">メール</th>
              <th className="px-4 py-3 text-left">種別</th>
              <th className="px-4 py-3 text-center">管理者</th>
              <th className="px-4 py-3 text-center">有効</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {users.map((u: User) => (
              <tr key={u.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td className="px-4 py-3 font-medium text-gray-800 dark:text-gray-200">{u.name}</td>
                <td className="px-4 py-3 text-gray-600 dark:text-gray-400">{u.email}</td>
                <td className="px-4 py-3 text-gray-500 dark:text-gray-400">{u.auth_type}</td>
                <td className="px-4 py-3 text-center">
                  {u.is_admin ? <span className="text-indigo-600 dark:text-indigo-400 font-medium">●</span> : <span className="text-gray-300 dark:text-gray-600">○</span>}
                </td>
                <td className="px-4 py-3 text-center">
                  <button
                    onClick={() => toggleActive.mutate(u)}
                    className={`px-2 py-0.5 text-xs rounded-full font-medium ${u.is_active ? 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-400' : 'bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-400'}`}
                  >
                    {u.is_active ? '有効' : '無効'}
                  </button>
                </td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => { if (confirm(`"${u.name}" を無効化しますか？`)) del.mutate(u.id) }}
                    className="text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────
// Allowed Emails tab
// ────────────────────────────────────────────────────────────
function AllowedEmailsTab() {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')

  const { data: entries = [] } = useQuery({
    queryKey: ['admin-allowed-emails'],
    queryFn: () => api.get('/users/allowed-emails/').then((r) => r.data),
  })

  const add = useMutation({
    mutationFn: () => api.post('/users/allowed-emails/', { email }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-allowed-emails'] }); setEmail('') },
  })

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/users/allowed-emails/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-allowed-emails'] }),
  })

  return (
    <div>
      <h2 className="text-base font-semibold text-gray-700 dark:text-gray-200 mb-4">Google OAuth 許可メール</h2>
      <div className="flex gap-2 mb-4">
        <input
          placeholder="example@gmail.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && email && add.mutate()}
          className="flex-1 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <button
          onClick={() => add.mutate()}
          disabled={!email || add.isPending}
          className="flex items-center gap-1.5 px-3 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" />追加
        </button>
      </div>
      <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400 text-xs uppercase">
            <tr>
              <th className="px-4 py-3 text-left">メールアドレス</th>
              <th className="px-4 py-3 text-left">登録日</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {entries.map((e: AllowedEmail) => (
              <tr key={e.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td className="px-4 py-3 text-gray-700 dark:text-gray-300">{e.email}</td>
                <td className="px-4 py-3 text-gray-400 dark:text-gray-500">{new Date(e.created_at).toLocaleDateString('ja-JP')}</td>
                <td className="px-4 py-3 text-right">
                  <button onClick={() => del.mutate(e.id)} className="text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr><td colSpan={3} className="px-4 py-8 text-center text-gray-400 dark:text-gray-500">許可メールがありません</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────
// MCP Keys tab
// ────────────────────────────────────────────────────────────
function McpKeysTab() {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [newKey, setNewKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const { data: keys = [] } = useQuery({
    queryKey: ['admin-mcp-keys'],
    queryFn: () => api.get('/mcp-keys').then((r) => r.data),
  })

  const create = useMutation({
    mutationFn: () => api.post('/mcp-keys', { name }),
    onSuccess: ({ data }) => {
      qc.invalidateQueries({ queryKey: ['admin-mcp-keys'] })
      setNewKey(data.key)
      setName('')
    },
  })

  const revoke = useMutation({
    mutationFn: (id: string) => api.delete(`/mcp-keys/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-mcp-keys'] }),
  })

  const copyKey = async () => {
    if (!newKey) return
    await navigator.clipboard.writeText(newKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div>
      <h2 className="text-base font-semibold text-gray-700 dark:text-gray-200 mb-4">MCP APIキー管理</h2>

      {newKey && (
        <div className="mb-4 p-4 border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/30 rounded-xl">
          <p className="text-sm text-green-700 dark:text-green-400 font-medium mb-2">キーが発行されました。この画面を閉じると再表示できません。</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs bg-white dark:bg-gray-800 border border-green-200 dark:border-green-800 rounded px-3 py-2 text-gray-800 dark:text-gray-200 font-mono break-all">
              {newKey}
            </code>
            <button onClick={copyKey} className="text-green-600 hover:text-green-800 dark:text-green-400 dark:hover:text-green-300">
              {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
            </button>
          </div>
          <button
            onClick={() => setNewKey(null)}
            className="mt-2 text-xs text-green-600 dark:text-green-400 underline"
          >
            閉じる
          </button>
        </div>
      )}

      <div className="flex gap-2 mb-4">
        <input
          placeholder="キー名（例: Claude Code Local）"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && name && create.mutate()}
          className="flex-1 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <button
          onClick={() => create.mutate()}
          disabled={!name || create.isPending}
          className="flex items-center gap-1.5 px-3 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
        >
          <Key className="w-4 h-4" />発行
        </button>
      </div>

      <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400 text-xs uppercase">
            <tr>
              <th className="px-4 py-3 text-left">名前</th>
              <th className="px-4 py-3 text-left">最終使用</th>
              <th className="px-4 py-3 text-left">発行日</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {keys.map((k: McpApiKey) => (
              <tr key={k.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td className="px-4 py-3 font-medium text-gray-800 dark:text-gray-200">{k.name}</td>
                <td className="px-4 py-3 text-gray-400 dark:text-gray-500">
                  {k.last_used_at ? new Date(k.last_used_at).toLocaleString('ja-JP') : '未使用'}
                </td>
                <td className="px-4 py-3 text-gray-400 dark:text-gray-500">{new Date(k.created_at).toLocaleDateString('ja-JP')}</td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => { if (confirm(`"${k.name}" を無効化しますか？`)) revoke.mutate(k.id) }}
                    className="text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </td>
              </tr>
            ))}
            {keys.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-8 text-center text-gray-400 dark:text-gray-500">APIキーがありません</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────
// Projects tab
// ────────────────────────────────────────────────────────────
function ProjectsTab() {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [color, setColor] = useState('#6366f1')

  const { data: projects = [] } = useQuery({
    queryKey: ['admin-projects'],
    queryFn: () => api.get('/projects').then((r) => r.data),
  })

  const create = useMutation({
    mutationFn: () => api.post('/projects', { name, description, color }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-projects'] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      setName(''); setDescription(''); setColor('#6366f1'); setShowForm(false)
    },
  })

  const archive = useMutation({
    mutationFn: (id: string) => api.patch(`/projects/${id}`, { status: 'archived' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-projects'] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-gray-700 dark:text-gray-200">プロジェクト管理</h2>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
        >
          <Plus className="w-4 h-4" />プロジェクト追加
        </button>
      </div>

      {showForm && (
        <div className="mb-4 p-4 border border-gray-200 dark:border-gray-600 rounded-xl bg-gray-50 dark:bg-gray-700 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <input
              placeholder="プロジェクト名"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <div className="flex items-center gap-2">
              <label className="text-sm text-gray-600 dark:text-gray-300">カラー</label>
              <input
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                className="w-8 h-8 rounded cursor-pointer border-0"
              />
              <span className="text-xs text-gray-400 dark:text-gray-500">{color}</span>
            </div>
            <input
              placeholder="説明（任意）"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="col-span-2 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowForm(false)} className="px-3 py-1.5 text-sm text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-600">キャンセル</button>
            <button
              onClick={() => create.mutate()}
              disabled={!name || create.isPending}
              className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {create.isPending ? '作成中...' : '作成'}
            </button>
          </div>
        </div>
      )}

      <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400 text-xs uppercase">
            <tr>
              <th className="px-4 py-3 text-left">プロジェクト</th>
              <th className="px-4 py-3 text-left">説明</th>
              <th className="px-4 py-3 text-center">メンバー</th>
              <th className="px-4 py-3 text-center">ステータス</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {projects.map((p: Project) => (
              <tr key={p.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: p.color ?? undefined }} />
                    <span className="font-medium text-gray-800 dark:text-gray-200">{p.name}</span>
                  </div>
                </td>
                <td className="px-4 py-3 text-gray-500 dark:text-gray-400 truncate max-w-xs">{p.description || '—'}</td>
                <td className="px-4 py-3 text-center text-gray-500 dark:text-gray-400">{p.members?.length ?? 0}</td>
                <td className="px-4 py-3 text-center">
                  <span className={`px-2 py-0.5 text-xs rounded-full font-medium ${p.status === 'active' ? 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-400' : 'bg-gray-100 dark:bg-gray-600 text-gray-500 dark:text-gray-400'}`}>
                    {p.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  {p.status === 'active' && (
                    <button
                      onClick={() => { if (confirm(`"${p.name}" をアーカイブしますか？`)) archive.mutate(p.id) }}
                      className="text-xs text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400"
                    >
                      アーカイブ
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ────────────────────────────────────────────────────────────
// AdminPage
// ────────────────────────────────────────────────────────────
const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: 'users', label: 'ユーザ', icon: <Users className="w-4 h-4" /> },
  { id: 'emails', label: '許可メール', icon: <Mail className="w-4 h-4" /> },
  { id: 'keys', label: 'MCPキー', icon: <Key className="w-4 h-4" /> },
  { id: 'projects', label: 'プロジェクト', icon: <FolderOpen className="w-4 h-4" /> },
]

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>('users')

  return (
    <div className="flex flex-col h-full">
      <div className="px-8 py-4 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <h1 className="text-xl font-bold text-gray-800 dark:text-gray-100">管理者設定</h1>
      </div>
      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-4xl mx-auto">
          <div className="flex gap-1 mb-6 border-b border-gray-200 dark:border-gray-700">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  tab === t.id
                    ? 'border-indigo-600 dark:border-indigo-400 text-indigo-600 dark:text-indigo-400'
                    : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
                }`}
              >
                {t.icon}{t.label}
              </button>
            ))}
          </div>
          {tab === 'users' && <UsersTab />}
          {tab === 'emails' && <AllowedEmailsTab />}
          {tab === 'keys' && <McpKeysTab />}
          {tab === 'projects' && <ProjectsTab />}
        </div>
      </div>
    </div>
  )
}
