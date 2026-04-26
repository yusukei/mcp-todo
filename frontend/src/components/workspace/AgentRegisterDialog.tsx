import { useState } from 'react'
import { X, Copy, Check } from 'lucide-react'
import { api } from '../../api/client'

interface AgentRegisterDialogProps {
  open: boolean
  onClose: () => void
  onCreated: () => void
}

export default function AgentRegisterDialog({ open, onClose, onCreated }: AgentRegisterDialogProps) {
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [createdToken, setCreatedToken] = useState<string | null>(null)
  const [copiedToken, setCopiedToken] = useState(false)
  const [copiedCmd, setCopiedCmd] = useState(false)

  if (!open) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return

    setLoading(true)
    setError('')
    try {
      const res = await api.post('/workspaces/agents', { name: name.trim() })
      setCreatedToken(res.data.token)
      onCreated()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to create agent')
    } finally {
      setLoading(false)
    }
  }

  const copyToClipboard = async (text: string, target: 'token' | 'cmd') => {
    await navigator.clipboard.writeText(text)
    if (target === 'token') {
      setCopiedToken(true)
      setTimeout(() => setCopiedToken(false), 2000)
    } else {
      setCopiedCmd(true)
      setTimeout(() => setCopiedCmd(false), 2000)
    }
  }

  const handleClose = () => {
    setName('')
    setCreatedToken(null)
    setError('')
    setCopiedToken(false)
    setCopiedCmd(false)
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={handleClose}>
      <div
        className="bg-gray-100 dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-md mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100">Agent 登録</h3>
          <button onClick={handleClose} className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-5 py-4">
          {createdToken ? (
            <div className="space-y-3">
              <p className="text-sm text-gray-600 dark:text-gray-300">
                Agent が登録されました。以下のトークンをコピーしてAgent設定に使用してください。
              </p>
              <p className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                このトークンは一度だけ表示されます。閉じると再表示できません。
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 bg-gray-100 dark:bg-gray-900 rounded text-xs font-mono text-gray-800 dark:text-gray-200 break-all select-all">
                  {createdToken}
                </code>
                <button
                  onClick={() => copyToClipboard(createdToken!, 'token')}
                  className="flex-shrink-0 p-2 rounded-lg bg-accent-50 dark:bg-accent-900/30 text-accent-600 dark:text-accent-400 hover:bg-accent-100 dark:hover:bg-accent-900/50"
                  title="トークンをコピー"
                >
                  {copiedToken ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
              <div className="relative mt-3 p-3 bg-gray-50 dark:bg-gray-900 rounded-lg text-xs text-gray-500 dark:text-gray-400 space-y-1">
                <div className="flex items-center justify-between mb-1">
                  <p className="font-medium text-gray-600 dark:text-gray-300">Agent の起動方法:</p>
                  <button
                    onClick={() => copyToClipboard(
                      `cd agent && uv run python main.py --url wss://${window.location.host}/api/v1/workspaces/agent/ws --token ${createdToken}`,
                      'cmd',
                    )}
                    className="p-1 rounded text-gray-400 hover:text-accent-500 dark:hover:text-accent-400"
                    title="コマンドをコピー"
                  >
                    {copiedCmd ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                </div>
                <code className="block whitespace-pre-wrap">
{`cd agent
uv run python main.py \\
  --url wss://${window.location.host}/api/v1/workspaces/agent/ws \\
  --token ${createdToken}`}
                </code>
              </div>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Agent 名
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="例: 開発用 Mac"
                  className="w-full px-3 py-2 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 text-sm focus:ring-2 focus:ring-focus focus:border-accent-500"
                  autoFocus
                  maxLength={100}
                />
              </div>
              {error && (
                <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
              )}
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={handleClose}
                  className="px-4 py-2 text-sm rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                >
                  キャンセル
                </button>
                <button
                  type="submit"
                  disabled={loading || !name.trim()}
                  className="px-4 py-2 text-sm rounded-lg bg-accent-500 text-gray-100 hover:bg-accent-600 disabled:opacity-50"
                >
                  {loading ? '登録中...' : '登録'}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
