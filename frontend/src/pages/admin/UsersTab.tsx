import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Plus, KeyRound } from 'lucide-react'
import { api } from '../../api/client'
import { showConfirm } from '../../components/common/ConfirmDialog'
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
    queryFn: () => api.get('/users').then((r) => r.data.items),
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

  const toggleAdmin = useMutation({
    mutationFn: (u: User) => api.patch(`/users/${u.id}`, { is_admin: !u.is_admin }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
    onError: () => showErrorToast('管理者権限の切り替えに失敗しました'),
  })

  const [resetResult, setResetResult] = useState<{ name: string; password: string } | null>(null)
  const [resetTarget, setResetTarget] = useState<User | null>(null)
  const [resetNewPassword, setResetNewPassword] = useState('')

  const resetPassword = useMutation({
    mutationFn: ({ user, newPassword }: { user: User; newPassword: string }) =>
      api.post(`/users/${user.id}/reset-password`, newPassword ? { password: newPassword } : {}).then((r) => ({ name: user.name, password: r.data.new_password })),
    onSuccess: (data) => {
      setResetResult(data)
      setResetTarget(null)
      setResetNewPassword('')
      qc.invalidateQueries({ queryKey: ['admin-users'] })
    },
    onError: () => showErrorToast('パスワードリセットに失敗しました'),
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
                  <button
                    onClick={() => toggleAdmin.mutate(u)}
                    className={`px-2 py-0.5 text-xs rounded-full font-medium ${u.is_admin ? 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-400' : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'}`}
                  >
                    {u.is_admin ? '管理者' : '一般'}
                  </button>
                </td>
                <td className="px-4 py-3 text-center">
                  <button
                    onClick={() => toggleActive.mutate(u)}
                    className={`px-2 py-0.5 text-xs rounded-full font-medium ${u.is_active ? 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-400' : 'bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-400'}`}
                  >
                    {u.is_active ? '有効' : '無効'}
                  </button>
                </td>
                <td className="px-4 py-3 text-right flex items-center justify-end gap-2">
                  {u.auth_type === 'admin' && (
                    <button
                      onClick={() => { setResetTarget(u); setResetNewPassword('') }}
                      className="text-gray-400 hover:text-indigo-500 dark:text-gray-500 dark:hover:text-indigo-400"
                      aria-label="パスワードリセット"
                      title="パスワードリセット"
                    >
                      <KeyRound className="w-4 h-4" />
                    </button>
                  )}
                  <button
                    onClick={async () => { if (await showConfirm(`"${u.name}" を無効化しますか？`)) del.mutate(u.id) }}
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
      {resetTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 w-full max-w-sm space-y-4">
            <h3 className="text-base font-semibold text-gray-800 dark:text-gray-100">パスワードリセット</h3>
            <p className="text-sm text-gray-600 dark:text-gray-300">
              <span className="font-medium">{resetTarget.name}</span> のパスワードをリセットします。
            </p>
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">新しいパスワード（空欄で自動生成）</label>
              <input
                type="text"
                value={resetNewPassword}
                onChange={(e) => setResetNewPassword(e.target.value)}
                placeholder="8文字以上"
                className="w-full border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                autoFocus
              />
              {resetNewPassword.length > 0 && resetNewPassword.length < 8 && (
                <p className="text-xs text-red-500 mt-1">8文字以上で入力してください</p>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setResetTarget(null)}
                disabled={resetPassword.isPending}
                className="px-4 py-2 text-sm bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-500 disabled:opacity-50"
              >
                キャンセル
              </button>
              <button
                onClick={() => resetPassword.mutate({ user: resetTarget, newPassword: resetNewPassword })}
                disabled={(resetNewPassword.length > 0 && resetNewPassword.length < 8) || resetPassword.isPending}
                className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {resetPassword.isPending ? 'リセット中...' : 'リセット'}
              </button>
            </div>
          </div>
        </div>
      )}
      {resetResult && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 w-full max-w-sm space-y-4">
            <h3 className="text-base font-semibold text-gray-800 dark:text-gray-100">パスワードリセット完了</h3>
            <p className="text-sm text-gray-600 dark:text-gray-300">
              <span className="font-medium">{resetResult.name}</span> の新しいパスワード:
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 px-3 py-2 bg-gray-100 dark:bg-gray-700 rounded-lg text-sm font-mono text-gray-800 dark:text-gray-200 select-all break-all">
                {resetResult.password}
              </code>
              <button
                onClick={() => navigator.clipboard.writeText(resetResult.password)}
                className="px-3 py-2 text-xs bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 shrink-0"
              >
                コピー
              </button>
            </div>
            <p className="text-xs text-amber-600 dark:text-amber-400">
              このパスワードは再表示できません。必ずコピーしてください。
            </p>
            <div className="flex justify-end">
              <button
                onClick={() => setResetResult(null)}
                className="px-4 py-2 text-sm bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg hover:bg-gray-300 dark:hover:bg-gray-500"
              >
                閉じる
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
