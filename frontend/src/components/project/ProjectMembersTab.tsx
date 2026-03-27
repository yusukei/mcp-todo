import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Crown, UserMinus, UserPlus, Search } from 'lucide-react'
import { api } from '../../api/client'
import { useAuthStore } from '../../store/auth'
import { showErrorToast, showSuccessToast } from '../common/Toast'
import type { Project, MemberRole } from '../../types'

interface UserResult {
  id: string
  name: string
  email: string
  picture_url?: string
}

export default function ProjectMembersTab({ project }: { project: Project }) {
  const qc = useQueryClient()
  const user = useAuthStore((s) => s.user)
  const isOwnerOrAdmin =
    user?.is_admin || project.members.some((m) => m.user_id === user?.id && m.role === 'owner')

  // ── Add member search ──────────────────────────────────
  const [searchQuery, setSearchQuery] = useState('')
  const [showSearch, setShowSearch] = useState(false)

  const { data: searchResults = [] } = useQuery<UserResult[]>({
    queryKey: ['user-search', searchQuery],
    queryFn: () => api.get(`/users/search/active?q=${encodeURIComponent(searchQuery)}&limit=10`).then((r) => r.data),
    enabled: showSearch && searchQuery.length >= 1,
  })

  const memberUserIds = new Set(project.members.map((m) => m.user_id))
  const filteredResults = searchResults.filter((u) => !memberUserIds.has(u.id))

  // ── Resolve member names ───────────────────────────────
  const memberIds = project.members.map((m) => m.user_id)
  const { data: memberUsers = [] } = useQuery<UserResult[]>({
    queryKey: ['project-member-users', project.id, memberIds.join(',')],
    queryFn: () => api.get(`/users/search/active?limit=50`).then((r) => r.data),
    enabled: project.members.length > 0,
  })
  const userMap = new Map(memberUsers.map((u) => [u.id, u]))

  // ── Mutations ──────────────────────────────────────────
  const addMember = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: MemberRole }) =>
      api.post(`/projects/${project.id}/members`, { user_id: userId, role }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', project.id] })
      showSuccessToast('メンバーを追加しました')
      setSearchQuery('')
      setShowSearch(false)
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showErrorToast(msg || 'メンバーの追加に失敗しました')
    },
  })

  const updateMemberRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: MemberRole }) =>
      api.patch(`/projects/${project.id}/members/${userId}`, { role }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', project.id] })
      showSuccessToast('ロールを変更しました')
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showErrorToast(msg || 'ロールの変更に失敗しました')
    },
  })

  const removeMember = useMutation({
    mutationFn: (userId: string) => api.delete(`/projects/${project.id}/members/${userId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', project.id] })
      showSuccessToast('メンバーを削除しました')
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showErrorToast(msg || 'メンバーの削除に失敗しました')
    },
  })

  return (
    <div className="max-w-3xl mx-auto p-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100">メンバー</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {project.members.length} 人のメンバー
          </p>
        </div>
        {isOwnerOrAdmin && !showSearch && (
          <button
            onClick={() => setShowSearch(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700"
          >
            <UserPlus className="w-4 h-4" />
            メンバー追加
          </button>
        )}
      </div>

      {/* Add member search */}
      {showSearch && isOwnerOrAdmin && (
        <div className="border border-indigo-200 dark:border-indigo-800 bg-indigo-50 dark:bg-indigo-900/20 rounded-xl p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="名前またはメールアドレスで検索..."
              autoFocus
              className="flex-1 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 text-gray-900 dark:text-gray-100"
            />
            <button
              onClick={() => { setShowSearch(false); setSearchQuery('') }}
              className="text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            >
              キャンセル
            </button>
          </div>
          {filteredResults.length > 0 && (
            <ul className="divide-y divide-gray-200 dark:divide-gray-700 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
              {filteredResults.map((u) => (
                <li key={u.id} className="flex items-center justify-between px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <div className="flex items-center gap-3">
                    {u.picture_url ? (
                      <img src={u.picture_url} alt="" className="w-8 h-8 rounded-full" />
                    ) : (
                      <div className="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-600 flex items-center justify-center text-gray-500 dark:text-gray-300 text-sm font-bold">
                        {u.name[0]?.toUpperCase()}
                      </div>
                    )}
                    <div>
                      <p className="text-sm font-medium text-gray-800 dark:text-gray-100">{u.name}</p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">{u.email}</p>
                    </div>
                  </div>
                  <div className="flex gap-1">
                    <button
                      onClick={() => addMember.mutate({ userId: u.id, role: 'member' })}
                      disabled={addMember.isPending}
                      className="px-3 py-1 text-xs bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-50"
                    >
                      メンバー
                    </button>
                    <button
                      onClick={() => addMember.mutate({ userId: u.id, role: 'owner' })}
                      disabled={addMember.isPending}
                      className="px-3 py-1 text-xs bg-amber-500 text-white rounded-md hover:bg-amber-600 disabled:opacity-50"
                    >
                      オーナー
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {searchQuery.length >= 1 && filteredResults.length === 0 && (
            <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-2">
              追加可能なユーザーが見つかりません
            </p>
          )}
        </div>
      )}

      {/* Member list */}
      <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400 text-xs uppercase">
            <tr>
              <th className="px-4 py-3 text-left">ユーザー</th>
              <th className="px-4 py-3 text-left">ロール</th>
              <th className="px-4 py-3 text-left">参加日</th>
              {isOwnerOrAdmin && <th className="px-4 py-3" />}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {project.members.map((m) => {
              const u = userMap.get(m.user_id)
              return (
                <tr key={m.user_id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      {u?.picture_url ? (
                        <img src={u.picture_url} alt="" className="w-8 h-8 rounded-full" />
                      ) : (
                        <div className="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-600 flex items-center justify-center text-gray-500 dark:text-gray-300 text-sm font-bold">
                          {(u?.name || '?')[0]?.toUpperCase()}
                        </div>
                      )}
                      <div>
                        <p className="font-medium text-gray-800 dark:text-gray-200">{u?.name || m.user_id}</p>
                        {u?.email && <p className="text-xs text-gray-500 dark:text-gray-400">{u.email}</p>}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {isOwnerOrAdmin && m.user_id !== user?.id ? (
                      <button
                        onClick={() => {
                          const newRole: MemberRole = m.role === 'owner' ? 'member' : 'owner'
                          if (confirm(`${u?.name || m.user_id} のロールを「${newRole === 'owner' ? 'オーナー' : 'メンバー'}」に変更しますか？`))
                            updateMemberRole.mutate({ userId: m.user_id, role: newRole })
                        }}
                        disabled={updateMemberRole.isPending}
                        title="クリックでロールを変更"
                        className="cursor-pointer disabled:opacity-50"
                      >
                        {m.role === 'owner' ? (
                          <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-900/30 px-2 py-0.5 rounded-full hover:bg-amber-200 dark:hover:bg-amber-900/50 transition-colors">
                            <Crown className="w-3 h-3" />
                            オーナー
                          </span>
                        ) : (
                          <span className="text-xs font-medium text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors">
                            メンバー
                          </span>
                        )}
                      </button>
                    ) : m.role === 'owner' ? (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-900/30 px-2 py-0.5 rounded-full">
                        <Crown className="w-3 h-3" />
                        オーナー
                      </span>
                    ) : (
                      <span className="text-xs font-medium text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded-full">
                        メンバー
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-400 dark:text-gray-500">
                    {new Date(m.joined_at).toLocaleDateString('ja-JP')}
                  </td>
                  {isOwnerOrAdmin && (
                    <td className="px-4 py-3 text-right">
                      {m.user_id !== user?.id && (
                        <button
                          onClick={() => {
                            if (confirm(`${u?.name || m.user_id} をプロジェクトから削除しますか？`))
                              removeMember.mutate(m.user_id)
                          }}
                          className="p-1.5 text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400"
                          title="メンバーを削除"
                        >
                          <UserMinus className="w-4 h-4" />
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
