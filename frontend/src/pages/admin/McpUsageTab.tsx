import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, AlertCircle, Eye, MessageSquarePlus, Trash2 } from 'lucide-react'
import { api } from '../../api/client'
import McpFeedbackSection from './McpFeedbackSection'

type SummaryItem = {
  tool_name: string
  count: number
  error_count: number
  error_rate: number
  avg_duration_ms: number
  max_duration_ms: number
  arg_size_sum: number
}

type SummaryResponse = {
  since: string
  days: number
  total_calls: number
  total_errors: number
  tool_count: number
  items: SummaryItem[]
}

type UnusedResponse = {
  days: number
  registered_count: number
  used_count: number
  unused_count: number
  unused: string[]
}

type ErrorEvent = {
  id: string
  ts: string
  tool_name: string
  api_key_id: string | null
  duration_ms: number
  success: boolean
  error_class: string | null
  arg_size_bytes: number
  reason: 'error' | 'slow' | 'sampled'
}

type HealthResponse = {
  enabled: boolean
  sampling_rate: number
  slow_call_ms: number
  registered_tools: number
  bucket_doc_count: number
  event_doc_count: number
}

const RANGE_OPTIONS = [
  { label: '24h', days: 1 },
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
]

function fmtMs(ms: number): string {
  if (!ms) return '-'
  if (ms < 1) return ms.toFixed(2) + 'ms'
  if (ms < 1000) return ms.toFixed(0) + 'ms'
  return (ms / 1000).toFixed(2) + 's'
}

function fmtBytes(b: number): string {
  if (b < 1024) return b + 'B'
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + 'KB'
  return (b / (1024 * 1024)).toFixed(1) + 'MB'
}

type SubTab = 'usage' | 'feedback'

export default function McpUsageTab() {
  const [subTab, setSubTab] = useState<SubTab>('usage')
  const [days, setDays] = useState(30)

  const { data: summary, isLoading: summaryLoading } = useQuery<SummaryResponse>({
    queryKey: ['mcp-usage-summary', days],
    queryFn: () => api.get(`/mcp/usage/summary?days=${days}`).then((r) => r.data),
  })

  const { data: unused } = useQuery<UnusedResponse>({
    queryKey: ['mcp-usage-unused', days],
    queryFn: () => api.get(`/mcp/usage/unused?days=${days}`).then((r) => r.data),
  })

  const { data: errors } = useQuery<{ items: ErrorEvent[] }>({
    queryKey: ['mcp-usage-errors'],
    queryFn: () => api.get('/mcp/usage/errors?only_errors=true&limit=20').then((r) => r.data),
  })

  const { data: health } = useQuery<HealthResponse>({
    queryKey: ['mcp-usage-health'],
    queryFn: () => api.get('/mcp/usage/health').then((r) => r.data),
  })

  const sortedByErrorRate = useMemo(() => {
    if (!summary) return []
    return [...summary.items]
      .filter((r) => r.count > 0)
      .sort((a, b) => b.error_rate - a.error_rate)
      .slice(0, 10)
  }, [summary])

  return (
    <div className="space-y-6">
      {/* Sub-tab switcher */}
      <div className="flex items-center gap-4 border-b border-gray-200 dark:border-gray-700">
        <button
          onClick={() => setSubTab('usage')}
          className={`flex items-center gap-1.5 px-1 pb-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            subTab === 'usage'
              ? 'border-accent-600 dark:border-accent-400 text-accent-600 dark:text-accent-400'
              : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
          }`}
        >
          <Activity className="w-4 h-4" />
          使用状況
        </button>
        <button
          onClick={() => setSubTab('feedback')}
          className={`flex items-center gap-1.5 px-1 pb-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
            subTab === 'feedback'
              ? 'border-accent-600 dark:border-accent-400 text-accent-600 dark:text-accent-400'
              : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
          }`}
        >
          <MessageSquarePlus className="w-4 h-4" />
          改善リクエスト
        </button>
      </div>

      {subTab === 'feedback' && <McpFeedbackSection />}

      {subTab === 'usage' && <>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-700 dark:text-gray-200">MCP ツール使用状況</h2>
        <div className="flex gap-1 text-xs">
          {RANGE_OPTIONS.map((o) => (
            <button
              key={o.days}
              onClick={() => setDays(o.days)}
              className={`px-2.5 py-1 rounded border ${
                days === o.days
                  ? 'bg-accent-500 text-gray-100 border-accent-600'
                  : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700'
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          icon={<Activity className="w-4 h-4" />}
          label="総呼び出し"
          value={summary?.total_calls ?? 0}
        />
        <StatCard
          icon={<AlertCircle className="w-4 h-4" />}
          label="エラー数"
          value={summary?.total_errors ?? 0}
          accent={summary && summary.total_errors > 0 ? 'red' : undefined}
        />
        <StatCard
          icon={<Eye className="w-4 h-4" />}
          label="登録ツール数"
          value={health?.registered_tools ?? summary?.tool_count ?? 0}
        />
        <StatCard
          icon={<Trash2 className="w-4 h-4" />}
          label="未使用ツール"
          value={unused?.unused_count ?? 0}
          accent={unused && unused.unused_count > 0 ? 'amber' : undefined}
        />
      </div>

      {/* Tool usage table */}
      <div>
        <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2">
          ツール別使用状況 (過去 {days} 日)
        </div>
        <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400 text-xs uppercase">
              <tr>
                <th className="px-3 py-2 text-left">ツール</th>
                <th className="px-3 py-2 text-right">呼び出し数</th>
                <th className="px-3 py-2 text-right">エラー</th>
                <th className="px-3 py-2 text-right">エラー率</th>
                <th className="px-3 py-2 text-right">avg</th>
                <th className="px-3 py-2 text-right">max</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {summaryLoading && (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-gray-400">
                    読み込み中…
                  </td>
                </tr>
              )}
              {summary?.items.map((r) => (
                <tr key={r.tool_name} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-3 py-2 font-mono text-xs text-gray-700 dark:text-gray-300">
                    {r.tool_name}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-700 dark:text-gray-300">
                    {r.count.toLocaleString()}
                  </td>
                  <td
                    className={`px-3 py-2 text-right tabular-nums ${
                      r.error_count > 0 ? 'text-red-500' : 'text-gray-400'
                    }`}
                  >
                    {r.error_count}
                  </td>
                  <td
                    className={`px-3 py-2 text-right tabular-nums ${
                      r.error_rate > 0.05 ? 'text-red-500' : 'text-gray-400'
                    }`}
                  >
                    {(r.error_rate * 100).toFixed(1)}%
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-500">
                    {fmtMs(r.avg_duration_ms)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-500">
                    {fmtMs(r.max_duration_ms)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Error rate top 10 + unused tools */}
      <div className="grid md:grid-cols-2 gap-4">
        <div>
          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2">
            エラー率 Top 10
          </div>
          <div className="border border-gray-200 dark:border-gray-700 rounded-xl divide-y divide-gray-100 dark:divide-gray-700">
            {sortedByErrorRate.length === 0 && (
              <div className="px-3 py-4 text-center text-gray-400 text-sm">エラーはありません</div>
            )}
            {sortedByErrorRate.map((r) => (
              <div
                key={r.tool_name}
                className="px-3 py-2 flex items-center justify-between text-sm"
              >
                <span className="font-mono text-xs text-gray-700 dark:text-gray-300">
                  {r.tool_name}
                </span>
                <span className="text-red-500 tabular-nums">
                  {(r.error_rate * 100).toFixed(1)}% ({r.error_count}/{r.count})
                </span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2">
            未使用ツール (削除候補)
          </div>
          <div className="border border-gray-200 dark:border-gray-700 rounded-xl">
            {unused && unused.unused.length === 0 && (
              <div className="px-3 py-4 text-center text-gray-400 text-sm">
                すべて使われています
              </div>
            )}
            {unused?.unused.map((name) => (
              <div
                key={name}
                className="px-3 py-2 font-mono text-xs text-gray-600 dark:text-gray-400 border-b border-gray-100 dark:border-gray-700 last:border-b-0"
              >
                {name}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Recent errors */}
      <div>
        <div className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase mb-2">
          最近のエラー / スローコール (個別イベント)
        </div>
        <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400 text-xs uppercase">
              <tr>
                <th className="px-3 py-2 text-left">時刻</th>
                <th className="px-3 py-2 text-left">ツール</th>
                <th className="px-3 py-2 text-left">理由</th>
                <th className="px-3 py-2 text-left">エラー</th>
                <th className="px-3 py-2 text-right">時間</th>
                <th className="px-3 py-2 text-right">引数サイズ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {(!errors || errors.items.length === 0) && (
                <tr>
                  <td colSpan={6} className="px-3 py-6 text-center text-gray-400">
                    エラーイベントはありません
                  </td>
                </tr>
              )}
              {errors?.items.map((e) => (
                <tr key={e.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-3 py-2 text-xs text-gray-500">
                    {new Date(e.ts).toLocaleString('ja-JP')}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-gray-700 dark:text-gray-300">
                    {e.tool_name}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-500">{e.reason}</td>
                  <td className="px-3 py-2 text-xs text-red-500">{e.error_class ?? '-'}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-500">
                    {fmtMs(e.duration_ms)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-500">
                    {fmtBytes(e.arg_size_bytes)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Health footer */}
      {health && (
        <div className="text-xs text-gray-400 dark:text-gray-500">
          計測: {health.enabled ? '有効' : '無効'} / サンプリング率{' '}
          {(health.sampling_rate * 100).toFixed(0)}% / スローコール閾値 {health.slow_call_ms}ms /
          バケット {health.bucket_doc_count.toLocaleString()} 件 / イベント{' '}
          {health.event_doc_count.toLocaleString()} 件
        </div>
      )}
      </>}
    </div>
  )
}

function StatCard({
  icon,
  label,
  value,
  accent,
}: {
  icon: React.ReactNode
  label: string
  value: number
  accent?: 'red' | 'amber'
}) {
  const accentClass =
    accent === 'red'
      ? 'text-red-500'
      : accent === 'amber'
        ? 'text-amber-500'
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
