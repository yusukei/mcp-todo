import { useState, useRef, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, X, Pencil, Lock, Unlock, Server, Trash2 } from 'lucide-react'
import { api } from '../api/client'
import { useAuthStore } from '../store/auth'
import ProjectMembersTab from '../components/project/ProjectMembersTab'
import { showErrorToast, showSuccessToast } from '../components/common/Toast'
import { showConfirm } from '../components/common/ConfirmDialog'

const COLOR_PRESETS = [
  '#6366f1', '#8b5cf6', '#ec4899', '#ef4444', '#f97316',
  '#eab308', '#22c55e', '#14b8a6', '#06b6d4', '#3b82f6',
]

export default function ProjectSettingsPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const qc = useQueryClient()
  const user = useAuthStore((s) => s.user)

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.get(`/projects/${projectId}`).then((r) => r.data),
    enabled: !!projectId,
  })

  const isOwnerOrAdmin =
    user?.is_admin ||
    project?.members?.some((m: { user_id: string; role: string }) => m.user_id === user?.id && m.role === 'owner')

  // ── Rename ───────────────────────────────────────────
  const [isRenaming, setIsRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const renameInputRef = useRef<HTMLInputElement>(null)

  const renameMutation = useMutation({
    mutationFn: (name: string) => api.patch(`/projects/${projectId}`, { name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      setIsRenaming(false)
    },
    onError: () => showErrorToast('プロジェクト名の変更に失敗しました'),
  })

  const startRename = () => {
    if (!project) return
    setRenameValue(project.name)
    setIsRenaming(true)
  }

  useEffect(() => {
    if (isRenaming && renameInputRef.current) {
      renameInputRef.current.focus()
      renameInputRef.current.select()
    }
  }, [isRenaming])

  const confirmRename = () => {
    const trimmed = renameValue.trim()
    if (trimmed && trimmed !== project?.name) {
      renameMutation.mutate(trimmed)
    } else {
      setIsRenaming(false)
    }
  }

  // ── Color ────────────────────────────────────────────
  const colorMutation = useMutation({
    mutationFn: (color: string) => api.patch(`/projects/${projectId}`, { color }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: () => showErrorToast('カラーの変更に失敗しました'),
  })

  // ── Remote binding ───────────────────────────────────
  const { data: agents = [] } = useQuery<Array<{ id: string; name: string; hostname: string; is_online: boolean }>>({
    queryKey: ['workspace-agents'],
    queryFn: () => api.get('/workspaces/agents').then((r) => r.data),
    enabled: isOwnerOrAdmin,
  })

  const [editingRemote, setEditingRemote] = useState(false)
  const [remoteAgentId, setRemoteAgentId] = useState('')
  const [remotePath, setRemotePath] = useState('')
  const [remoteLabel, setRemoteLabel] = useState('')
  const [remoteError, setRemoteError] = useState('')

  const startEditRemote = () => {
    if (project?.remote) {
      setRemoteAgentId(project.remote.agent_id)
      setRemotePath(project.remote.remote_path)
      setRemoteLabel(project.remote.label || '')
    } else {
      setRemoteAgentId(agents[0]?.id || '')
      setRemotePath('')
      setRemoteLabel('')
    }
    setRemoteError('')
    setEditingRemote(true)
  }

  const setRemoteMutation = useMutation({
    mutationFn: (body: { agent_id: string; remote_path: string; label: string }) =>
      api.put(`/projects/${projectId}/remote`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      setEditingRemote(false)
      showSuccessToast('リモートエージェントを設定しました')
    },
    onError: (err: any) => {
      setRemoteError(err?.response?.data?.detail || 'リモート設定の更新に失敗しました')
    },
  })

  const clearRemoteMutation = useMutation({
    mutationFn: () => api.delete(`/projects/${projectId}/remote`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      showSuccessToast('リモートエージェント設定を解除しました')
    },
    onError: () => showErrorToast('リモート設定の解除に失敗しました'),
  })

  const handleSaveRemote = () => {
    if (!remoteAgentId || !remotePath.trim()) {
      setRemoteError('エージェントとリモートパスは必須です')
      return
    }
    setRemoteMutation.mutate({
      agent_id: remoteAgentId,
      remote_path: remotePath.trim(),
      label: remoteLabel.trim(),
    })
  }

  const handleClearRemote = async () => {
    if (await showConfirm('このプロジェクトからリモートエージェント設定を解除しますか？')) {
      clearRemoteMutation.mutate()
    }
  }

  const boundAgent = project?.remote
    ? agents.find((a) => a.id === project.remote.agent_id)
    : null

  // ── Lock ─────────────────────────────────────────────
  const lockMutation = useMutation({
    mutationFn: (locked: boolean) => api.patch(`/projects/${projectId}`, { is_locked: locked }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['project', projectId] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: () => showErrorToast('ロック状態の変更に失敗しました'),
  })

  if (!project) return <div className="p-8 text-gray-500 dark:text-gray-400">読み込み中...</div>

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto p-8 space-y-8">
        {/* Header */}
        <div className="flex items-center gap-3">
          <Link
            to={`/projects/${projectId}`}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            title="プロジェクトに戻る"
          >
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-xl font-bold text-gray-800 dark:text-gray-100">プロジェクト設定</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">{project.name}</p>
          </div>
        </div>

        {/* General settings */}
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100">基本設定</h2>
          <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 divide-y divide-gray-100 dark:divide-gray-700">
            {/* Project name */}
            <div className="px-6 py-4 flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-700 dark:text-gray-300">プロジェクト名</p>
                {isRenaming ? (
                  <div className="flex items-center gap-2 mt-1">
                    <input
                      ref={renameInputRef}
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') confirmRename()
                        if (e.key === 'Escape') setIsRenaming(false)
                      }}
                      maxLength={255}
                      className="bg-white dark:bg-gray-700 border border-indigo-400 rounded-lg px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 text-gray-900 dark:text-gray-100"
                    />
                    <button onClick={confirmRename} disabled={renameMutation.isPending} className="p-1 text-green-600 hover:bg-green-50 dark:hover:bg-green-900/30 rounded">
                      <Check className="w-4 h-4" />
                    </button>
                    <button onClick={() => setIsRenaming(false)} className="p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded">
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ) : (
                  <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{project.name}</p>
                )}
              </div>
              {!isRenaming && isOwnerOrAdmin && (
                <button
                  onClick={startRename}
                  className="p-2 text-gray-400 hover:text-indigo-500 dark:hover:text-indigo-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
                >
                  <Pencil className="w-4 h-4" />
                </button>
              )}
            </div>

            {/* Color */}
            <div className="px-6 py-4">
              <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">カラー</p>
              <div className="flex items-center gap-2">
                {COLOR_PRESETS.map((c) => (
                  <button
                    key={c}
                    onClick={() => isOwnerOrAdmin && colorMutation.mutate(c)}
                    disabled={!isOwnerOrAdmin}
                    className={`w-7 h-7 rounded-full border-2 transition-all ${
                      project.color === c
                        ? 'border-gray-800 dark:border-white scale-110'
                        : 'border-transparent hover:scale-110'
                    } ${!isOwnerOrAdmin ? 'cursor-default' : 'cursor-pointer'}`}
                    style={{ backgroundColor: c }}
                  />
                ))}
              </div>
            </div>

            {/* Lock */}
            {isOwnerOrAdmin && (
              <div className="px-6 py-4 flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-gray-700 dark:text-gray-300">プロジェクトロック</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                    ロック中はタスクの作成・編集ができません
                  </p>
                </div>
                <button
                  onClick={() => lockMutation.mutate(!project.is_locked)}
                  disabled={lockMutation.isPending}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg transition-colors ${
                    project.is_locked
                      ? 'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400 hover:bg-amber-200 dark:hover:bg-amber-900/60'
                      : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                  }`}
                >
                  {project.is_locked ? <Lock className="w-4 h-4" /> : <Unlock className="w-4 h-4" />}
                  {project.is_locked ? 'ロック中' : 'アンロック'}
                </button>
              </div>
            )}
          </div>
        </section>

        {/* Remote agent binding */}
        <section className="space-y-4">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
            <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100">リモートエージェント</h2>
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            このプロジェクトを特定のリモートエージェント＋ディレクトリに紐付けると、
            Chat やリモート MCP ツール（<code className="text-xs">remote_exec</code> など）
            がそのパスを作業ディレクトリとして動作します。
          </p>
          <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 px-6 py-4">
            {editingRemote ? (
              <div className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                    エージェント
                  </label>
                  <select
                    value={remoteAgentId}
                    onChange={(e) => setRemoteAgentId(e.target.value)}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200"
                  >
                    <option value="">選択してください</option>
                    {agents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} ({a.hostname || 'unknown'}) {a.is_online ? '● online' : '○ offline'}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                    リモートパス <span className="text-red-500">*</span>
                  </label>
                  <input
                    value={remotePath}
                    onChange={(e) => setRemotePath(e.target.value)}
                    placeholder="/home/user/projects/my-project"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 font-mono focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
                    ラベル（任意）
                  </label>
                  <input
                    value={remoteLabel}
                    onChange={(e) => setRemoteLabel(e.target.value)}
                    placeholder="例: production server"
                    className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
                {remoteError && (
                  <p className="text-sm text-red-600 dark:text-red-400">{remoteError}</p>
                )}
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => setEditingRemote(false)}
                    className="px-3 py-1.5 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
                  >
                    キャンセル
                  </button>
                  <button
                    onClick={handleSaveRemote}
                    disabled={setRemoteMutation.isPending}
                    className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {setRemoteMutation.isPending ? '保存中...' : '保存'}
                  </button>
                </div>
              </div>
            ) : project.remote ? (
              <div className="flex items-start justify-between">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-gray-800 dark:text-gray-100">
                      {boundAgent?.name || '(不明なエージェント)'}
                    </span>
                    {project.remote.label && (
                      <span className="text-xs text-gray-400 dark:text-gray-500">
                        ({project.remote.label})
                      </span>
                    )}
                    {boundAgent && (
                      <span
                        className={`inline-flex items-center px-1.5 py-0.5 text-xs rounded-full ${
                          boundAgent.is_online
                            ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                            : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
                        }`}
                      >
                        {boundAgent.is_online ? 'online' : 'offline'}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 font-mono truncate">
                    {project.remote.remote_path}
                  </p>
                </div>
                {isOwnerOrAdmin && (
                  <div className="flex items-center gap-1 flex-shrink-0 ml-4">
                    <button
                      onClick={startEditRemote}
                      className="p-2 rounded-lg text-gray-400 hover:text-indigo-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                      title="編集"
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button
                      onClick={handleClearRemote}
                      className="p-2 rounded-lg text-gray-400 hover:text-red-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                      title="解除"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  リモートエージェント未設定
                </p>
                {isOwnerOrAdmin && (
                  <button
                    onClick={startEditRemote}
                    disabled={agents.length === 0}
                    className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    title={agents.length === 0 ? 'エージェントが登録されていません' : ''}
                  >
                    設定
                  </button>
                )}
              </div>
            )}
          </div>
        </section>

        {/* Members */}
        <section>
          <ProjectMembersTab project={project} />
        </section>
      </div>
    </div>
  )
}
