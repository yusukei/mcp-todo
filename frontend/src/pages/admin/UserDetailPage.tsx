/**
 * Phase 6.A-3: admin user-detail page.
 *
 * Routed at ``/admin/users/:userId``. Shows the user's profile,
 * 30-day stats, project memberships, and recent MCP tool usage —
 * all driven by Phase 6.B endpoints (``GET /users/:id``,
 * ``GET /users/:id/projects``, ``GET /users/:id/ai_runs``).
 *
 * Auth: gated by ``AdminRoute`` in App.tsx. Non-admins never reach
 * this component.
 */
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Mail, Activity, FolderOpen, Bot, Calendar, ShieldCheck, ShieldOff } from 'lucide-react'
import { usersApi } from '../../api/users'
import type { UserStatus } from '../../types'

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

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('ja-JP', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function StatCard({
  label,
  value,
  icon,
  hint,
}: {
  label: string
  value: string | number
  icon: React.ReactNode
  hint?: string
}) {
  return (
    <div className="rounded-very border border-gray-700 bg-gray-800 p-4">
      <div className="flex items-center gap-2 text-xs font-mono uppercase tracking-wider text-gray-300">
        {icon}
        {label}
      </div>
      <div className="mt-2 font-serif text-3xl text-gray-50 leading-tight-serif">{value}</div>
      {hint && <div className="mt-1 text-xs text-gray-300">{hint}</div>}
    </div>
  )
}

export default function UserDetailPage() {
  const { userId = '' } = useParams<{ userId: string }>()

  const userQ = useQuery({
    queryKey: ['admin-user', userId],
    queryFn: () => usersApi.get(userId),
    enabled: !!userId,
  })

  const projectsQ = useQuery({
    queryKey: ['admin-user-projects', userId],
    queryFn: () => usersApi.projects(userId),
    enabled: !!userId,
  })

  const aiRunsQ = useQuery({
    queryKey: ['admin-user-ai-runs', userId],
    queryFn: () => usersApi.aiRuns(userId),
    enabled: !!userId,
  })

  if (!userId) {
    return (
      <div className="p-8 text-gray-300">
        ユーザ ID が指定されていません。{' '}
        <Link to="/admin" className="text-accent-400 underline">
          ユーザ一覧に戻る
        </Link>
      </div>
    )
  }

  if (userQ.isLoading) {
    return <div className="p-8 text-gray-300">読み込み中...</div>
  }

  if (userQ.isError || !userQ.data) {
    return (
      <div className="p-8">
        <p className="text-pri-urgent mb-3">ユーザを取得できませんでした。</p>
        <Link
          to="/admin"
          className="inline-flex items-center gap-1 text-sm text-accent-400 hover:text-accent-300"
        >
          <ArrowLeft className="w-4 h-4" />
          ユーザ一覧に戻る
        </Link>
      </div>
    )
  }

  const user = userQ.data
  const status: UserStatus =
    (user.status as UserStatus | undefined) ??
    (user.is_active ? 'active' : 'suspended')

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="px-8 py-4 border-b border-gray-700 bg-gray-800 flex items-center gap-4">
        <Link
          to="/admin"
          className="inline-flex items-center gap-1 text-sm text-gray-200 hover:text-accent-400 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          ユーザ一覧
        </Link>
        <h1 className="text-xl font-serif text-gray-50 leading-tight-serif">{user.name}</h1>
        <span className={`inline-block px-2 py-0.5 text-xs rounded-full font-medium ${STATUS_BADGE[status]}`}>
          {STATUS_LABELS[status]}
        </span>
      </div>

      <div className="flex-1 max-w-5xl mx-auto w-full p-8 space-y-8">
        {/* Profile card */}
        <section>
          <header className="mb-3">
            <h2 className="font-serif text-lg text-gray-50">プロフィール</h2>
          </header>
          <div className="rounded-very border border-gray-700 bg-gray-800 p-5 flex items-start gap-5">
            {user.picture_url ? (
              <img
                src={user.picture_url}
                alt={user.name}
                className="w-16 h-16 rounded-full border border-gray-600 object-cover"
              />
            ) : (
              <div className="w-16 h-16 rounded-full bg-gray-700 flex items-center justify-center text-2xl font-serif text-gray-100">
                {user.name.charAt(0)}
              </div>
            )}
            <div className="flex-1 grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
              <div>
                <div className="text-xs font-mono uppercase tracking-wider text-gray-300">メール</div>
                <div className="mt-0.5 flex items-center gap-1.5 text-gray-50">
                  <Mail className="w-3.5 h-3.5 text-gray-300" />
                  {user.email}
                </div>
              </div>
              <div>
                <div className="text-xs font-mono uppercase tracking-wider text-gray-300">認証</div>
                <div className="mt-0.5 text-gray-50 font-mono">{user.auth_type}</div>
              </div>
              <div>
                <div className="text-xs font-mono uppercase tracking-wider text-gray-300">権限</div>
                <div className="mt-0.5 flex items-center gap-1.5 text-gray-50">
                  {user.is_admin ? (
                    <>
                      <ShieldCheck className="w-3.5 h-3.5 text-accent-400" />
                      管理者
                    </>
                  ) : (
                    <>
                      <ShieldOff className="w-3.5 h-3.5 text-gray-300" />
                      一般
                    </>
                  )}
                </div>
              </div>
              <div>
                <div className="text-xs font-mono uppercase tracking-wider text-gray-300">最終アクティブ</div>
                <div className="mt-0.5 text-gray-50">{formatDateTime(user.last_active_at)}</div>
              </div>
              <div>
                <div className="text-xs font-mono uppercase tracking-wider text-gray-300">作成日</div>
                <div className="mt-0.5 text-gray-50">{formatDateTime(user.created_at)}</div>
              </div>
              <div>
                <div className="text-xs font-mono uppercase tracking-wider text-gray-300">ユーザ ID</div>
                <div className="mt-0.5 font-mono text-xs text-gray-300 select-all">{user.id}</div>
              </div>
            </div>
          </div>
        </section>

        {/* 30-day stats */}
        <section>
          <header className="mb-3">
            <h2 className="font-serif text-lg text-gray-50">30 日間の活動</h2>
          </header>
          <div className="grid grid-cols-3 gap-4">
            <StatCard
              label="AI 実行数"
              value={user.ai_runs_30d ?? 0}
              icon={<Bot className="w-3.5 h-3.5" />}
              hint="MCP ツール呼び出し合計"
            />
            <StatCard
              label="参加プロジェクト"
              value={user.projects_count ?? 0}
              icon={<FolderOpen className="w-3.5 h-3.5" />}
              hint="メンバーになっている数"
            />
            <StatCard
              label="ステータス"
              value={STATUS_LABELS[status]}
              icon={<Activity className="w-3.5 h-3.5" />}
              hint={
                user.last_active_at
                  ? `最終: ${formatDateTime(user.last_active_at)}`
                  : 'まだアクセスなし'
              }
            />
          </div>
        </section>

        {/* Project memberships */}
        <section>
          <header className="mb-3 flex items-center justify-between">
            <h2 className="font-serif text-lg text-gray-50">所属プロジェクト</h2>
            {projectsQ.data && (
              <span className="text-xs font-mono text-gray-300">
                {projectsQ.data.length} 件
              </span>
            )}
          </header>
          {projectsQ.isLoading ? (
            <div className="text-sm text-gray-300">読み込み中...</div>
          ) : projectsQ.isError ? (
            <div className="text-sm text-pri-urgent">プロジェクト一覧を取得できませんでした。</div>
          ) : projectsQ.data && projectsQ.data.length > 0 ? (
            <div className="rounded-very border border-gray-700 bg-gray-800 divide-y divide-gray-700 overflow-hidden">
              {projectsQ.data.map((p) => (
                <Link
                  key={p.id}
                  to={`/projects/${p.id}`}
                  className="flex items-center gap-3 px-4 py-3 hover:bg-gray-700/40 transition-colors"
                >
                  <span
                    className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                    style={{ backgroundColor: p.color }}
                  />
                  <span className="flex-1 text-sm text-gray-50 font-medium">{p.name}</span>
                  <span className="text-xs text-gray-300 font-mono">
                    {p.role ?? '—'}
                  </span>
                  <span className="text-xs text-gray-300 font-mono">
                    members: {p.member_count}
                  </span>
                  <span className="text-xs text-gray-300 inline-flex items-center gap-1">
                    <Calendar className="w-3 h-3" />
                    {p.created_at ? new Date(p.created_at).toLocaleDateString('ja-JP') : '—'}
                  </span>
                </Link>
              ))}
            </div>
          ) : (
            <div className="rounded-very border border-gray-700 bg-gray-800 p-6 text-center text-sm text-gray-300">
              所属しているプロジェクトはありません
            </div>
          )}
        </section>

        {/* Recent AI tool usage */}
        <section>
          <header className="mb-3 flex items-center justify-between">
            <h2 className="font-serif text-lg text-gray-50">最近の AI ツール利用</h2>
            <span className="text-xs font-mono text-gray-300">過去 30 日</span>
          </header>
          {aiRunsQ.isLoading ? (
            <div className="text-sm text-gray-300">読み込み中...</div>
          ) : aiRunsQ.isError ? (
            <div className="text-sm text-pri-urgent">AI 実行ログを取得できませんでした。</div>
          ) : aiRunsQ.data && aiRunsQ.data.by_tool.length > 0 ? (
            <div className="rounded-very border border-gray-700 bg-gray-800 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-900/40 text-xs font-mono uppercase tracking-wider text-gray-300">
                  <tr>
                    <th className="px-4 py-2 text-left">ツール名</th>
                    <th className="px-4 py-2 text-right">呼び出し回数</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-700">
                  {aiRunsQ.data.by_tool.map((row) => (
                    <tr key={row.tool_name} className="hover:bg-gray-700/40">
                      <td className="px-4 py-2 font-mono text-gray-50">{row.tool_name}</td>
                      <td className="px-4 py-2 text-right font-mono text-gray-100">
                        {row.call_count.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className="bg-gray-900/40 text-xs font-mono uppercase tracking-wider text-gray-200">
                  <tr>
                    <td className="px-4 py-2">合計</td>
                    <td className="px-4 py-2 text-right text-accent-400">
                      {aiRunsQ.data.total_calls.toLocaleString()}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          ) : (
            <div className="rounded-very border border-gray-700 bg-gray-800 p-6 text-center text-sm text-gray-300">
              過去 30 日に AI ツール呼び出しはありません
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
