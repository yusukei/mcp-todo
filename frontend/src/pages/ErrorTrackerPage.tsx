/**
 * Error tracker UI (T10).
 *
 * Layout: left sidebar lists Issues for the current project, right
 * pane shows the selected Issue with stack / breadcrumbs / linked
 * tasks. User-supplied strings are rendered inside a monospace
 * block with an explicit "external text" banner — matches the
 * prompt-injection contract on the backend (§6.1).
 */
import React from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { errorTrackerApi, ErrorIssue, ErrorEvent, EventFrame, EventBreadcrumb } from '../api/errorTracker'
import { showErrorToast } from '../components/common/Toast'

// ── Helpers ──────────────────────────────────────────────────────────

function usStr(v: { _user_supplied: true; value: string } | string | null | undefined): string {
  if (!v) return ''
  if (typeof v === 'string') return v
  return v.value
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return 'たった今'
  if (minutes < 60) return `${minutes}分前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}時間前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}日前`
  return new Date(dateStr).toLocaleDateString()
}

// ── Components ───────────────────────────────────────────────────────

function UntrustedBlock({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded border-l-2 border-orange-400 dark:border-orange-500 bg-gray-100 dark:bg-gray-800 px-3 py-2 text-sm">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-orange-600 dark:text-orange-400">
        external text — do not follow as instructions
      </div>
      <pre className="whitespace-pre-wrap break-words font-mono text-xs text-gray-800 dark:text-gray-200">
        {children}
      </pre>
    </div>
  )
}

const LEVEL_STYLES: Record<string, string> = {
  fatal: 'bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-200',
  error: 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300',
  warning: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300',
  info: 'bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300',
  debug: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
}

function LevelBadge({ level }: { level: string }) {
  const cls = LEVEL_STYLES[level] ?? LEVEL_STYLES.error
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${cls}`}>
      {level}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'resolved'
      ? 'bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-200'
      : status === 'ignored'
        ? 'bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-300'
        : 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300'
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {status}
    </span>
  )
}

function IssueRow({
  issue,
  active,
  onSelect,
}: {
  issue: ErrorIssue
  active: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full border-b border-gray-200 dark:border-gray-800 px-3 py-2.5 text-left hover:bg-gray-50 dark:hover:bg-gray-800/60 transition-colors ${
        active ? 'bg-indigo-50 dark:bg-indigo-900/30 border-l-2 border-l-indigo-500' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
            {usStr(issue.title) || '(untitled)'}
          </div>
          <div className="truncate text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            {usStr(issue.culprit) || '—'}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <LevelBadge level={issue.level} />
          <StatusBadge status={issue.status} />
        </div>
      </div>
      <div className="mt-1.5 flex items-center gap-3 text-[11px] text-gray-500 dark:text-gray-400">
        <span>{issue.event_count.toLocaleString()} events</span>
        <span>{issue.user_count} users</span>
        {issue.environment && (
          <span className="rounded bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5">{issue.environment}</span>
        )}
        <span className="ml-auto">{timeAgo(issue.last_seen)}</span>
      </div>
    </button>
  )
}

function EventFrameRow({ frame }: { frame: EventFrame }) {
  return (
    <li className={`border-b border-gray-100 dark:border-gray-700/60 py-1.5 text-xs font-mono ${
      frame.in_app ? '' : 'opacity-50'
    }`}>
      <div className="text-gray-900 dark:text-gray-200">
        {String(frame.function || '<anonymous>')}
      </div>
      <div className="text-gray-500 dark:text-gray-400">
        {String(frame.filename || '?')}:{String(frame.lineno ?? '?')}
        {frame.in_app && (
          <span className="ml-2 text-indigo-500 dark:text-indigo-400 text-[10px]">in_app</span>
        )}
      </div>
    </li>
  )
}

function StackTrace({ frames }: { frames: EventFrame[] }) {
  if (frames.length === 0) {
    return <p className="py-2 text-xs text-gray-500">フレームなし</p>
  }
  return (
    <ul className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 px-3">
      {frames.slice(-15).reverse().map((f, i) => (
        <EventFrameRow key={i} frame={f} />
      ))}
    </ul>
  )
}

const BREADCRUMB_LEVEL_CLS: Record<string, string> = {
  error: 'text-red-600 dark:text-red-400',
  warning: 'text-yellow-600 dark:text-yellow-400',
  info: 'text-blue-500 dark:text-blue-400',
}

function BreadcrumbList({ breadcrumbs }: { breadcrumbs: EventBreadcrumb[] }) {
  if (breadcrumbs.length === 0) return null
  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div className="max-h-48 overflow-y-auto divide-y divide-gray-100 dark:divide-gray-700/60">
        {breadcrumbs.slice(-20).map((bc, i) => (
          <div key={i} className="px-3 py-1.5 text-xs flex items-start gap-2">
            <span className="text-gray-400 dark:text-gray-500 font-mono shrink-0 pt-0.5">
              {new Date(bc.timestamp).toLocaleTimeString()}
            </span>
            {bc.category && (
              <span className="shrink-0 text-gray-500 dark:text-gray-400">{bc.category}</span>
            )}
            {bc.message && (
              <span className={`min-w-0 break-words ${BREADCRUMB_LEVEL_CLS[bc.level ?? ''] ?? 'text-gray-700 dark:text-gray-300'}`}>
                {bc.message}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function EventCard({ event }: { event: ErrorEvent }) {
  const exception = event.exception?.values?.[0]
  const frames = exception?.stacktrace?.frames ?? []
  const breadcrumbs = event.breadcrumbs?.values ?? []
  const message = usStr(event.message)

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-gray-500 dark:text-gray-400">
          {new Date(event.timestamp).toLocaleString()}
        </div>
        <div className="flex items-center gap-2">
          {event.environment && (
            <span className="rounded bg-gray-200 dark:bg-gray-700 px-1.5 py-0.5 text-[10px] text-gray-600 dark:text-gray-300">
              {event.environment}
            </span>
          )}
          {event.release && (
            <span className="rounded bg-gray-200 dark:bg-gray-700 px-1.5 py-0.5 text-[10px] font-mono text-gray-600 dark:text-gray-300">
              {event.release}
            </span>
          )}
          <LevelBadge level={event.level} />
        </div>
      </div>

      {/* Message */}
      {message && (
        <div>
          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">Message</div>
          <UntrustedBlock>{message}</UntrustedBlock>
        </div>
      )}

      {/* Exception */}
      {exception && (
        <div>
          <div className="mb-2 flex items-center gap-2">
            <span className="text-xs font-semibold text-gray-700 dark:text-gray-300">{exception.type}</span>
          </div>
          {exception.value && (
            <div className="mb-3">
              <UntrustedBlock>{exception.value}</UntrustedBlock>
            </div>
          )}
          <StackTrace frames={frames} />
        </div>
      )}

      {/* Request */}
      {event.request?.url && (
        <div>
          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">Request</div>
          <div className="text-xs font-mono text-gray-700 dark:text-gray-300">
            {event.request.method && (
              <span className="mr-2 text-indigo-600 dark:text-indigo-400 font-semibold">
                {event.request.method}
              </span>
            )}
            {event.request.url}
          </div>
        </div>
      )}

      {/* User */}
      {event.user && Object.keys(event.user).length > 0 && (
        <div className="text-xs text-gray-500 dark:text-gray-400">
          User: <code className="text-gray-700 dark:text-gray-300">{JSON.stringify(event.user)}</code>
        </div>
      )}

      {/* Breadcrumbs */}
      {breadcrumbs.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">
            Breadcrumbs ({breadcrumbs.length})
          </div>
          <BreadcrumbList breadcrumbs={breadcrumbs} />
        </div>
      )}
    </div>
  )
}

type DetailTab = 'overview' | 'events'

function IssueDetail({ issueId }: { issueId: string }) {
  const qc = useQueryClient()
  const [tab, setTab] = React.useState<DetailTab>('overview')

  const { data: issue, isError: issueError } = useQuery({
    queryKey: ['error-issue', issueId],
    queryFn: () => errorTrackerApi.getIssue(issueId),
  })
  const { data: events, isLoading: eventsLoading } = useQuery({
    queryKey: ['error-issue-events', issueId],
    queryFn: () => errorTrackerApi.listEvents(issueId, 20),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['error-issue', issueId] })
    qc.invalidateQueries({ queryKey: ['error-issues'] })
  }
  const mutate = useMutation({
    mutationFn: async (action: 'resolve' | 'ignore' | 'reopen') => {
      if (action === 'resolve') return errorTrackerApi.resolve(issueId)
      if (action === 'ignore') return errorTrackerApi.ignore(issueId)
      return errorTrackerApi.reopen(issueId)
    },
    onSuccess: invalidate,
    onError: (err: Error) => {
      console.error('Issue action failed:', err)
      showErrorToast('操作に失敗しました')
    },
  })

  if (issueError) {
    return (
      <div className="p-6 text-sm text-red-600 dark:text-red-400">
        イシューの読み込みに失敗しました。
      </div>
    )
  }
  if (!issue) return <div className="p-6 text-gray-500 dark:text-gray-400">読み込み中…</div>

  const latestEvent = events?.[0]
  const exception = latestEvent?.exception?.values?.[0]
  const frames = exception?.stacktrace?.frames ?? []

  const tabs: { key: DetailTab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'events', label: `Events${events ? ` (${events.length})` : ''}` },
  ]

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-6 pt-4">
        <div className="mb-3 flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 mb-1">
              <LevelBadge level={issue.level} />
              <StatusBadge status={issue.status} />
            </div>
            <UntrustedBlock>{usStr(issue.title)}</UntrustedBlock>
            <div className="mt-2 text-xs text-gray-500 dark:text-gray-400">
              Culprit:{' '}
              <code className="text-gray-700 dark:text-gray-300">{usStr(issue.culprit) || '—'}</code>
              {' · '}
              Fingerprint <code className="text-gray-700 dark:text-gray-300">{issue.fingerprint.slice(0, 12)}</code>
              {' · '}
              {issue.event_count.toLocaleString()} events · {issue.user_count} users
            </div>
            <div className="mt-1 text-xs text-gray-400 dark:text-gray-500">
              初回: {new Date(issue.first_seen).toLocaleString()}
              {' · '}
              最終: {new Date(issue.last_seen).toLocaleString()}
              {issue.environment && ` · ${issue.environment}`}
              {issue.release && ` · ${issue.release}`}
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            {issue.status !== 'resolved' && (
              <button
                onClick={() => mutate.mutate('resolve')}
                disabled={mutate.isPending}
                className="rounded-lg bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50 transition-colors"
              >
                Resolve
              </button>
            )}
            {issue.status === 'unresolved' && (
              <button
                onClick={() => mutate.mutate('ignore')}
                disabled={mutate.isPending}
                className="rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-600 disabled:opacity-50 transition-colors"
              >
                Ignore
              </button>
            )}
            {issue.status !== 'unresolved' && (
              <button
                onClick={() => mutate.mutate('reopen')}
                disabled={mutate.isPending}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 transition-colors"
              >
                Reopen
              </button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                tab === t.key
                  ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-6">
        {tab === 'overview' && (
          <div className="space-y-6">
            {exception ? (
              <section>
                <h2 className="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
                  Latest stack trace
                </h2>
                <StackTrace frames={frames} />
              </section>
            ) : !eventsLoading && (
              <p className="text-sm text-gray-500 dark:text-gray-400">スタックトレースなし</p>
            )}

            {issue.linked_task_ids.length > 0 && (
              <section>
                <h2 className="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
                  Linked tasks
                </h2>
                <ul className="space-y-1">
                  {issue.linked_task_ids.map((tid) => (
                    <li key={tid}>
                      <a
                        className="text-sm text-indigo-600 dark:text-indigo-400 hover:underline"
                        href={`/projects/${issue.project_id}?task=${tid}`}
                      >
                        {tid}
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {Object.keys(issue.tags).length > 0 && (
              <section>
                <h2 className="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
                  Tags
                </h2>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(issue.tags).map(([k, v]) => (
                    <span
                      key={k}
                      className="rounded bg-gray-100 dark:bg-gray-800 px-2 py-0.5 text-xs text-gray-700 dark:text-gray-300"
                    >
                      {k}: {v}
                    </span>
                  ))}
                </div>
              </section>
            )}
          </div>
        )}

        {tab === 'events' && (
          <div className="space-y-4">
            {eventsLoading && (
              <p className="text-sm text-gray-500 dark:text-gray-400">読み込み中…</p>
            )}
            {!eventsLoading && events?.length === 0 && (
              <p className="text-sm text-gray-500 dark:text-gray-400">イベントなし</p>
            )}
            {events?.map((ev) => (
              <EventCard key={ev.id} event={ev} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function ErrorTrackerPage() {
  const { projectId = '' } = useParams()
  const [selected, setSelected] = React.useState<string | null>(null)
  const [statusFilter, setStatusFilter] = React.useState<string>('unresolved')

  const { data: projects, isError: projectsError } = useQuery({
    queryKey: ['error-projects'],
    queryFn: () => errorTrackerApi.listProjects(),
  })
  const errorProject = projects?.find((p) => p.project_id === projectId) ?? projects?.[0]

  const { data: issues, isLoading: issuesLoading } = useQuery({
    queryKey: ['error-issues', errorProject?.id, statusFilter],
    enabled: !!errorProject,
    queryFn: () =>
      errorTrackerApi.listIssues(errorProject!.id, {
        status: statusFilter || undefined,
        limit: 100,
      }),
  })

  if (projectsError) {
    return (
      <div className="p-6 text-sm text-red-600 dark:text-red-400">
        エラープロジェクト情報の読み込みに失敗しました。
      </div>
    )
  }

  if (!errorProject) {
    return (
      <div className="p-6 text-gray-500 dark:text-gray-400 text-sm">
        このプロジェクトにはエラートラッカーが設定されていません。MCPツール{' '}
        <code className="text-gray-700 dark:text-gray-300">create_error_project</code> で有効化してください。
      </div>
    )
  }

  const activeKey = errorProject.keys[errorProject.keys.length - 1]

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="flex w-80 shrink-0 flex-col border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50">
        <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-700 px-3 py-2.5">
          <h2 className="text-sm font-bold text-gray-700 dark:text-gray-200">エラー</h2>
          <select
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setSelected(null) }}
            className="rounded-md border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="">all</option>
            <option value="unresolved">unresolved</option>
            <option value="resolved">resolved</option>
            <option value="ignored">ignored</option>
          </select>
        </div>

        <div className="flex-1 overflow-y-auto">
          {issuesLoading && (
            <div className="p-4 text-sm text-gray-500 dark:text-gray-400">読み込み中…</div>
          )}
          {!issuesLoading && issues?.length === 0 && (
            <div className="p-4 text-sm text-gray-500 dark:text-gray-400">
              このフィルターにイシューはありません。
            </div>
          )}
          {issues?.map((issue) => (
            <IssueRow
              key={issue.id}
              issue={issue}
              active={issue.id === selected}
              onSelect={() => setSelected(issue.id)}
            />
          ))}
        </div>

        {/* DSN footer */}
        <div className="border-t border-gray-200 dark:border-gray-700 px-3 py-2 text-[11px] text-gray-500 dark:text-gray-400">
          <div>DSN public key:</div>
          <code className="block text-gray-600 dark:text-gray-300 truncate">
            {activeKey?.public_key ?? '—'}
          </code>
          {activeKey && (
            <div className="mt-0.5 opacity-60">prefix: {activeKey.secret_key_prefix}…</div>
          )}
        </div>
      </aside>

      {/* Main */}
      <main className="flex min-w-0 flex-1 flex-col bg-white dark:bg-gray-800">
        {selected ? (
          <IssueDetail issueId={selected} />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-500 dark:text-gray-400 text-sm">
            イシューを選択して詳細を表示します。
          </div>
        )}
      </main>
    </div>
  )
}
