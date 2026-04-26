import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Plus, KeyRound } from 'lucide-react'
import { api } from '../../api/client'
import { showConfirm } from '../../components/common/ConfirmDialog'
import { showErrorToast } from '../../components/common/Toast'
import type { User, UserStatus } from '../../types'

// Phase 6.A-2: Monokai status palette for the lifecycle column.
// Falls back to the legacy is_active boolean when ``status`` is
// missing (older deployments).
const STATUS_LABELS: Record<UserStatus, string> = {
  active: '稼働中',
  invited: '招待中',
  suspended: '停止',
}

const STATUS_BADGE: Record<UserStatus, string> = {
  active: 'bg-status-done/15 text-status-done',
  invited: 'bg-status-progress/15 text-status-progress',
  suspended: 'bg-status-cancel/15 text-status-cancel',
}

function formatLastActive(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diff = Date.now() - then
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour
  if (diff < hour) return `${Math.max(1, Math.floor(diff / minute))}分前`
  if (diff < day) return `${Math.floor(diff / hour)}時間前`
  if (diff < 30 * day) return `${Math.floor(diff / day)}日前`
  return new Date(iso).toLocaleDateString('ja-JP')
}

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
        <h2 className="text-base font-semibold text-gray-50 font-serif">ユーザ管理</h2>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-accent-500 text-gray-50 rounded-comfortable hover:bg-accent-600"
        >
          <Plus className="w-4 h-4" />ユーザ追加
        </button>
      </div>

      {showForm && (
        <div className="mb-4 p-4 border border-gray-700 rounded-very bg-gray-800 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <input
              placeholder="メールアドレス"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="border border-gray-600 rounded-comfortable px-3 py-2 text-sm bg-gray-900 text-gray-50 focus:outline-none focus:ring-2 focus:ring-focus"
            />
            <input
              placeholder="名前"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="border border-gray-600 rounded-comfortable px-3 py-2 text-sm bg-gray-900 text-gray-50 focus:outline-none focus:ring-2 focus:ring-focus"
            />
            <input
              type="password"
              placeholder="パスワード"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="border border-gray-600 rounded-comfortable px-3 py-2 text-sm bg-gray-900 text-gray-50 focus:outline-none focus:ring-2 focus:ring-focus"
            />
            <label className="flex items-center gap-2 text-sm text-gray-200">
              <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
              管理者権限
            </label>
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowForm(false)} className="px-3 py-1.5 text-sm text-gray-200 border border-gray-600 rounded-comfortable hover:bg-gray-700">キャンセル</button>
            <button
              onClick={() => create.mutate()}
              disabled={!email || !name || create.isPending}
              className="px-3 py-1.5 text-sm bg-accent-500 text-gray-50 rounded-comfortable hover:bg-accent-600 disabled:opacity-50"
            >
              {create.isPending ? '作成中...' : '作成'}
            </button>
          </div>
        </div>
      )}

      <div className="border border-gray-700 rounded-very overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-800 text-gray-300 text-xs uppercase font-mono tracking-wider">
            <tr>
              <th className="px-4 py-3 text-left">名前</th>
              <th className="px-4 py-3 text-left">メール</th>
              <th className="px-4 py-3 text-left">状態</th>
              <th className="px-4 py-3 text-left">最終アクティブ</th>
              <th className="px-4 py-3 text-right">30d AI</th>
              <th className="px-4 py-3 text-right">参加 P</th>
              <th className="px-4 py-3 text-center">管理者</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700">
            {users.map((u: User) => {
              const status: UserStatus =
                (u.status as UserStatus | undefined) ??
                (u.is_active ? 'active' : 'suspended')
              return (
                <tr key={u.id} className="hover:bg-gray-700/40">
                  <td className="px-4 py-3 font-medium">
                    <Link
                      to={`/admin/users/${u.id}`}
                      className="text-gray-50 hover:text-accent-400 transition-colors"
                    >
                      {u.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-gray-300 font-mono text-xs">{u.email}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-block px-2 py-0.5 text-xs rounded-full font-medium ${STATUS_BADGE[status]}`}>
                      {STATUS_LABELS[status]}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-300 text-xs">{formatLastActive(u.last_active_at)}</td>
                  <td className="px-4 py-3 text-right text-gray-100 font-mono">
                    {u.ai_runs_30d ?? 0}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-100 font-mono">
                    {u.projects_count ?? 0}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <button
                      onClick={() => toggleAdmin.mutate(u)}
                      className={`px-2 py-0.5 text-xs rounded-full font-medium ${
                        u.is_admin
                          ? 'bg-accent-900/40 text-accent-300'
                          : 'bg-gray-700 text-gray-300'
                      }`}
                    >
                      {u.is_admin ? '管理者' : '一般'}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => toggleActive.mutate(u)}
                        className="text-xs text-gray-300 hover:text-accent-400"
                        title={u.is_active ? '無効化' : '有効化'}
                      >
                        {u.is_active ? '無効化' : '有効化'}
                      </button>
                      {u.auth_type === 'admin' && (
                        <button
                          onClick={() => { setResetTarget(u); setResetNewPassword('') }}
                          className="text-gray-300 hover:text-accent-400"
                          aria-label="パスワードリセット"
                          title="パスワードリセット"
                        >
                          <KeyRound className="w-4 h-4" />
                        </button>
                      )}
                      <button
                        onClick={async () => { if (await showConfirm(`「${u.name}」を削除しますか？\nこの操作は取り消せません。`)) del.mutate(u.id) }}
                        className="text-gray-300 hover:text-pri-urgent"
                        aria-label="削除"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {resetTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-gray-800 rounded-very shadow-whisper p-6 w-full max-w-sm space-y-4 border border-gray-700">
            <h3 className="text-base font-semibold text-gray-50 font-serif">パスワードリセット</h3>
            <p className="text-sm text-gray-200">
              <span className="font-medium">{resetTarget.name}</span> のパスワードをリセットします。
            </p>
            <div>
              <label className="block text-xs text-gray-300 mb-1">新しいパスワード（空欄で自動生成）</label>
              <input
                type="text"
                value={resetNewPassword}
                onChange={(e) => setResetNewPassword(e.target.value)}
                placeholder="8文字以上"
                className="w-full border border-gray-600 rounded-comfortable px-3 py-2 text-sm bg-gray-900 text-gray-50 focus:outline-none focus:ring-2 focus:ring-focus"
                autoFocus
              />
              {resetNewPassword.length > 0 && resetNewPassword.length < 8 && (
                <p className="text-xs text-pri-urgent mt-1">8文字以上で入力してください</p>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setResetTarget(null)}
                disabled={resetPassword.isPending}
                className="px-4 py-2 text-sm bg-gray-700 text-gray-100 rounded-comfortable hover:bg-gray-600 disabled:opacity-50"
              >
                キャンセル
              </button>
              <button
                onClick={() => resetPassword.mutate({ user: resetTarget, newPassword: resetNewPassword })}
                disabled={(resetNewPassword.length > 0 && resetNewPassword.length < 8) || resetPassword.isPending}
                className="px-4 py-2 text-sm bg-accent-500 text-gray-50 rounded-comfortable hover:bg-accent-600 disabled:opacity-50"
              >
                {resetPassword.isPending ? 'リセット中...' : 'リセット'}
              </button>
            </div>
          </div>
        </div>
      )}
      {resetResult && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-gray-800 rounded-very shadow-whisper p-6 w-full max-w-sm space-y-4 border border-gray-700">
            <h3 className="text-base font-semibold text-gray-50 font-serif">パスワードリセット完了</h3>
            <p className="text-sm text-gray-200">
              <span className="font-medium">{resetResult.name}</span> の新しいパスワード:
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 px-3 py-2 bg-gray-900 rounded-comfortable text-sm font-mono text-gray-50 select-all break-all">
                {resetResult.password}
              </code>
              <button
                onClick={() => navigator.clipboard.writeText(resetResult.password)}
                className="px-3 py-2 text-xs bg-accent-500 text-gray-50 rounded-comfortable hover:bg-accent-600 shrink-0"
              >
                コピー
              </button>
            </div>
            <p className="text-xs text-status-hold">
              このパスワードは再表示できません。必ずコピーしてください。
            </p>
            <div className="flex justify-end">
              <button
                onClick={() => setResetResult(null)}
                className="px-4 py-2 text-sm bg-gray-700 text-gray-100 rounded-comfortable hover:bg-gray-600"
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
