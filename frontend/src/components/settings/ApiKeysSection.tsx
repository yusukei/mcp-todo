import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Key, Copy, Check } from 'lucide-react'
import { api } from '../../api/client'
import { showConfirm } from '../common/ConfirmDialog'
import { showErrorToast, showSuccessToast } from '../../components/common/Toast'
import type { McpApiKey } from '../../types'

export default function ApiKeysSection() {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [newKey, setNewKey] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const { data: keys = [] } = useQuery<McpApiKey[]>({
    queryKey: ['my-api-keys'],
    queryFn: () => api.get('/mcp-keys').then((r) => r.data),
  })

  const create = useMutation({
    mutationFn: () => api.post('/mcp-keys', { name }),
    onSuccess: ({ data }) => {
      qc.invalidateQueries({ queryKey: ['my-api-keys'] })
      setNewKey(data.key)
      setName('')
    },
    onError: () => showErrorToast('APIキーの作成に失敗しました'),
  })

  const revoke = useMutation({
    mutationFn: (id: string) => api.delete(`/mcp-keys/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-api-keys'] })
      showSuccessToast('APIキーを無効化しました')
    },
    onError: () => showErrorToast('APIキーの削除に失敗しました'),
  })

  const copyKey = async () => {
    if (!newKey) return
    await navigator.clipboard.writeText(newKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-1">APIキー</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          MCP サーバーへのアクセスに使用するAPIキーを管理します。
        </p>
      </div>

      {newKey && (
        <div className="p-4 border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/30 rounded-xl">
          <p className="text-sm text-green-700 dark:text-green-400 font-medium mb-2">キーが発行されました。この画面を閉じると再表示できません。</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs bg-white dark:bg-gray-800 border border-green-200 dark:border-green-800 rounded px-3 py-2 text-gray-800 dark:text-gray-200 font-mono break-all">
              {newKey}
            </code>
            <button onClick={copyKey} className="text-green-600 hover:text-green-800 dark:text-green-400 dark:hover:text-green-300" aria-label="コピー">
              {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
            </button>
          </div>
          <button onClick={() => setNewKey(null)} className="mt-2 text-xs text-green-600 dark:text-green-400 underline">
            閉じる
          </button>
        </div>
      )}

      <div className="flex gap-2">
        <input
          placeholder="キー名（例: Claude Code Local）"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && name && create.mutate()}
          className="flex-1 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <button
          onClick={() => create.mutate()}
          disabled={!name || create.isPending}
          className="flex items-center gap-1.5 px-3 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 font-medium"
        >
          <Key className="w-4 h-4" />発行
        </button>
      </div>

      {keys.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400 py-4 text-center">
          APIキーがありません
        </p>
      ) : (
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
              {keys.map((k) => (
                <tr key={k.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                  <td className="px-4 py-3 font-medium text-gray-800 dark:text-gray-200">{k.name}</td>
                  <td className="px-4 py-3 text-gray-400 dark:text-gray-500">
                    {k.last_used_at ? new Date(k.last_used_at).toLocaleString('ja-JP') : '未使用'}
                  </td>
                  <td className="px-4 py-3 text-gray-400 dark:text-gray-500">{new Date(k.created_at).toLocaleDateString('ja-JP')}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={async () => { if (await showConfirm(`"${k.name}" を無効化しますか？`)) revoke.mutate(k.id) }}
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
      )}
    </div>
  )
}
