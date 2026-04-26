import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  MessageSquarePlus,
  CheckCircle2,
  XCircle,
  Clock,
  ChevronDown,
  ChevronUp,
  ThumbsUp,
  ArrowRight,
} from 'lucide-react'
import { api } from '../../api/client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FeedbackItem = {
  id: string
  tool_name: string
  request_type: string
  description: string
  related_tools: string[]
  status: string
  votes: number
  submitted_by: string | null
  created_at: string
  updated_at: string
}

type FeedbackListResponse = {
  total: number
  items: FeedbackItem[]
}

type FeedbackSummaryResponse = {
  by_status: Record<string, number>
  by_type: { request_type: string; count: number }[]
  top_tools_with_open_requests: {
    tool_name: string
    open_count: number
    total_votes: number
  }[]
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  open: {
    label: 'Open',
    color: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
    icon: <Clock className="w-3 h-3" />,
  },
  accepted: {
    label: 'Accepted',
    color: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
    icon: <CheckCircle2 className="w-3 h-3" />,
  },
  rejected: {
    label: 'Rejected',
    color: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    icon: <XCircle className="w-3 h-3" />,
  },
  done: {
    label: 'Done',
    color: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
    icon: <CheckCircle2 className="w-3 h-3" />,
  },
}

const TYPE_LABELS: Record<string, string> = {
  missing_param: 'パラメータ不足',
  merge: '統合',
  split: '分割',
  deprecate: '廃止',
  bug: 'バグ',
  performance: 'パフォーマンス',
  other: 'その他',
}

const STATUS_OPTIONS = ['open', 'accepted', 'rejected', 'done'] as const

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function McpFeedbackSection() {
  const queryClient = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<string>('open')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data: summary } = useQuery<FeedbackSummaryResponse>({
    queryKey: ['mcp-feedback-summary'],
    queryFn: () => api.get('/mcp/usage/feedback/summary').then((r) => r.data),
  })

  const { data: feedbackList, isLoading } = useQuery<FeedbackListResponse>({
    queryKey: ['mcp-feedback-list', statusFilter],
    queryFn: () =>
      api
        .get(`/mcp/usage/feedback?status=${statusFilter}&limit=100`)
        .then((r) => r.data),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, params }: { id: string; params: string }) =>
      api.patch(`/mcp/usage/feedback/${id}?${params}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcp-feedback-list'] })
      queryClient.invalidateQueries({ queryKey: ['mcp-feedback-summary'] })
    },
  })

  const handleStatusChange = (id: string, newStatus: string) => {
    updateMutation.mutate({ id, params: `status=${newStatus}` })
  }

  const handleVote = (id: string) => {
    updateMutation.mutate({ id, params: 'votes_delta=1' })
  }

  const totalOpen = summary?.by_status?.open ?? 0
  const totalAccepted = summary?.by_status?.accepted ?? 0
  const totalDone = summary?.by_status?.done ?? 0
  const totalRejected = summary?.by_status?.rejected ?? 0

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-700 dark:text-gray-200">
          API 改善リクエスト
        </h2>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <FeedbackStatCard
          icon={<MessageSquarePlus className="w-4 h-4" />}
          label="Open"
          value={totalOpen}
          accent={totalOpen > 0 ? 'blue' : undefined}
        />
        <FeedbackStatCard
          icon={<CheckCircle2 className="w-4 h-4" />}
          label="Accepted"
          value={totalAccepted}
          accent={totalAccepted > 0 ? 'green' : undefined}
        />
        <FeedbackStatCard
          icon={<CheckCircle2 className="w-4 h-4" />}
          label="Done"
          value={totalDone}
        />
        <FeedbackStatCard
          icon={<XCircle className="w-4 h-4" />}
          label="Rejected"
          value={totalRejected}
        />
      </div>

      {/* Type breakdown + Top tools */}
      <div className="grid md:grid-cols-2 gap-4">
        {/* By type */}
        <div>
          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2">
            タイプ別件数
          </div>
          <div className="border border-gray-200 dark:border-gray-700 rounded-xl divide-y divide-gray-100 dark:divide-gray-700">
            {(!summary || summary.by_type.length === 0) && (
              <div className="px-3 py-4 text-center text-gray-400 text-sm">
                リクエストはありません
              </div>
            )}
            {summary?.by_type.map((t) => (
              <div
                key={t.request_type}
                className="px-3 py-2 flex items-center justify-between text-sm"
              >
                <span className="text-gray-700 dark:text-gray-300">
                  {TYPE_LABELS[t.request_type] ?? t.request_type}
                </span>
                <span className="tabular-nums text-gray-500">{t.count}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Top tools with open requests */}
        <div>
          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2">
            リクエストが多いツール
          </div>
          <div className="border border-gray-200 dark:border-gray-700 rounded-xl divide-y divide-gray-100 dark:divide-gray-700">
            {(!summary || summary.top_tools_with_open_requests.length === 0) && (
              <div className="px-3 py-4 text-center text-gray-400 text-sm">
                Open なリクエストはありません
              </div>
            )}
            {summary?.top_tools_with_open_requests.map((t) => (
              <div
                key={t.tool_name}
                className="px-3 py-2 flex items-center justify-between text-sm"
              >
                <span className="font-mono text-xs text-gray-700 dark:text-gray-300">
                  {t.tool_name}
                </span>
                <div className="flex items-center gap-2">
                  <span className="tabular-nums text-gray-500">
                    {t.open_count}件
                  </span>
                  <span className="tabular-nums text-gray-400 text-xs flex items-center gap-0.5">
                    <ThumbsUp className="w-3 h-3" />
                    {t.total_votes}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Feedback list */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase">
            リクエスト一覧
          </div>
          <div className="flex gap-1 text-xs">
            {STATUS_OPTIONS.map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-2.5 py-1 rounded border ${
                  statusFilter === s
                    ? 'bg-accent-500 text-gray-100 border-accent-600'
                    : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
                }`}
              >
                {STATUS_CONFIG[s]?.label ?? s}
              </button>
            ))}
          </div>
        </div>

        <div className="border border-gray-200 dark:border-gray-700 rounded-xl divide-y divide-gray-100 dark:divide-gray-700">
          {isLoading && (
            <div className="px-3 py-6 text-center text-gray-400">読み込み中...</div>
          )}
          {!isLoading && (!feedbackList || feedbackList.items.length === 0) && (
            <div className="px-3 py-6 text-center text-gray-400 text-sm">
              {statusFilter === 'open' ? 'Open なリクエストはありません' : '該当するリクエストはありません'}
            </div>
          )}
          {feedbackList?.items.map((item) => {
            const isExpanded = expandedId === item.id
            const cfg = STATUS_CONFIG[item.status]

            return (
              <div key={item.id} className="group">
                {/* Summary row */}
                <div
                  className="px-3 py-2.5 flex items-center gap-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50"
                  onClick={() => setExpandedId(isExpanded ? null : item.id)}
                >
                  <div className="flex-shrink-0">
                    {isExpanded ? (
                      <ChevronUp className="w-4 h-4 text-gray-400" />
                    ) : (
                      <ChevronDown className="w-4 h-4 text-gray-400" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="font-mono text-xs font-medium text-gray-700 dark:text-gray-300">
                        {item.tool_name}
                      </span>
                      {item.related_tools.length > 0 && (
                        <span className="text-gray-400 flex items-center gap-1 text-xs">
                          <ArrowRight className="w-3 h-3" />
                          {item.related_tools.join(', ')}
                        </span>
                      )}
                    </div>
                    <div className="text-sm text-gray-600 dark:text-gray-400 truncate">
                      {item.description}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400">
                      {TYPE_LABELS[item.request_type] ?? item.request_type}
                    </span>
                    {cfg && (
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full flex items-center gap-1 ${cfg.color}`}
                      >
                        {cfg.icon}
                        {cfg.label}
                      </span>
                    )}
                    <span className="text-xs text-gray-400 tabular-nums flex items-center gap-0.5">
                      <ThumbsUp className="w-3 h-3" />
                      {item.votes}
                    </span>
                  </div>
                </div>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="px-3 pb-3 pl-10 space-y-3">
                    <div className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap bg-gray-50 dark:bg-gray-800 rounded-lg p-3">
                      {item.description}
                    </div>
                    <div className="flex items-center gap-4 text-xs text-gray-500">
                      <span>
                        送信: {new Date(item.created_at).toLocaleString('ja-JP')}
                      </span>
                      {item.submitted_by && (
                        <span className="font-mono">{item.submitted_by.slice(0, 16)}...</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          handleVote(item.id)
                        }}
                        className="text-xs px-2.5 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 flex items-center gap-1"
                      >
                        <ThumbsUp className="w-3 h-3" />
                        +1
                      </button>
                      <div className="border-l border-gray-200 dark:border-gray-700 h-4" />
                      {item.status !== 'accepted' && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            handleStatusChange(item.id, 'accepted')
                          }}
                          className="text-xs px-2.5 py-1 rounded bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 hover:bg-green-100 dark:hover:bg-green-900/40"
                        >
                          Accept
                        </button>
                      )}
                      {item.status !== 'rejected' && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            handleStatusChange(item.id, 'rejected')
                          }}
                          className="text-xs px-2.5 py-1 rounded bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/40"
                        >
                          Reject
                        </button>
                      )}
                      {item.status !== 'done' && item.status !== 'open' && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            handleStatusChange(item.id, 'done')
                          }}
                          className="text-xs px-2.5 py-1 rounded bg-gray-50 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-600"
                        >
                          Done
                        </button>
                      )}
                      {item.status !== 'open' && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            handleStatusChange(item.id, 'open')
                          }}
                          className="text-xs px-2.5 py-1 rounded bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/40"
                        >
                          Reopen
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {feedbackList && feedbackList.total > feedbackList.items.length && (
          <div className="text-xs text-gray-400 mt-2 text-center">
            {feedbackList.total} 件中 {feedbackList.items.length} 件を表示
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

function FeedbackStatCard({
  icon,
  label,
  value,
  accent,
}: {
  icon: React.ReactNode
  label: string
  value: number
  accent?: 'blue' | 'green'
}) {
  const accentClass =
    accent === 'blue'
      ? 'text-blue-600 dark:text-blue-400'
      : accent === 'green'
        ? 'text-green-600 dark:text-green-400'
        : 'text-gray-700 dark:text-gray-200'
  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-xl p-3">
      <div className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 mb-1">
        {icon}
        {label}
      </div>
      <div className={`text-2xl font-semibold tabular-nums ${accentClass}`}>
        {value.toLocaleString()}
      </div>
    </div>
  )
}
