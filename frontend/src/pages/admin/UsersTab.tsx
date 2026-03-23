import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Plus } from 'lucide-react'
import { api } from '../../api/client'
import { showErrorToast } from '../../components/common/Toast'
import type { User } from '../../types'

export default function UsersTab() {
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
    onError: () => showErrorToast('ユーザの作成に失敗しました'),
  })

  const toggleActive = useMutation({
    mutationFn: (u: User) => api.patch(`/users/${u.id}`, { is_active: !u.is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
    onError: () => showErrorToast('ユーザの有効/無効切り替えに失敗しました'),
  })

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/users/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
    onError: () => showErrorToast('ユーザの削除に失敗しました'),
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
                    aria-label="削除"
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
