import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Server, RefreshCw, Trash2, Pencil, FolderOpen } from 'lucide-react'
import { api } from '../api/client'
import AgentList, { type Agent } from '../components/workspace/AgentList'
import AgentRegisterDialog from '../components/workspace/AgentRegisterDialog'
import { showErrorToast, showSuccessToast } from '../components/common/Toast'
import { showConfirm } from '../components/common/ConfirmDialog'

interface Workspace {
  id: string
  agent_id: string
  agent_name: string
  project_id: string
  project_name: string
  remote_path: string
  label: string
  is_online: boolean
  created_at: string
  updated_at: string
}

interface Project {
  id: string
  name: string
}

export default function WorkspacePage() {
  const qc = useQueryClient()
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null)
  const [showRegister, setShowRegister] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editPath, setEditPath] = useState('')
  const [editLabel, setEditLabel] = useState('')

  // ── Queries ──────────────────────────────────────────
  const { data: agents = [], isLoading: agentsLoading } = useQuery({
    queryKey: ['workspace-agents'],
    queryFn: () => api.get('/workspaces/agents').then((r) => r.data),
    refetchInterval: 10000,
  })

  const { data: workspaces = [] } = useQuery<Workspace[]>({
    queryKey: ['workspaces'],
    queryFn: () => api.get('/workspaces').then((r) => r.data),
  })

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: () => api.get('/projects').then((r) => r.data),
  })

  // ── Mutations ────────────────────────────────────────
  const deleteMutation = useMutation({
    mutationFn: (agentId: string) => api.delete(`/workspaces/agents/${agentId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspace-agents'] })
      showSuccessToast('Agent を削除しました')
    },
    onError: () => showErrorToast('Agent の削除に失敗しました'),
  })

  const deleteWorkspaceMutation = useMutation({
    mutationFn: (wsId: string) => api.delete(`/workspaces/${wsId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspaces'] })
      showSuccessToast('ワークスペースを削除しました')
    },
    onError: () => showErrorToast('ワークスペースの削除に失敗しました'),
  })

  const updateWorkspaceMutation = useMutation({
    mutationFn: ({ id, ...body }: { id: string; remote_path?: string; label?: string }) =>
      api.patch(`/workspaces/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspaces'] })
      setEditingId(null)
    },
    onError: () => showErrorToast('ワークスペースの更新に失敗しました'),
  })

  // ── Handlers ─────────────────────────────────────────
  const handleSelectAgent = useCallback((agent: Agent) => {
    setSelectedAgent(agent)
  }, [])

  const handleDeleteAgent = useCallback(async (agentId: string) => {
    if (await showConfirm('この Agent を削除しますか？')) {
      deleteMutation.mutate(agentId)
      if (selectedAgent?.id === agentId) setSelectedAgent(null)
    }
  }, [deleteMutation, selectedAgent])

  const handleDeleteWorkspace = useCallback(async (wsId: string) => {
    if (await showConfirm('このワークスペースを削除しますか？')) {
      deleteWorkspaceMutation.mutate(wsId)
    }
  }, [deleteWorkspaceMutation])

  const startEdit = (ws: Workspace) => {
    setEditingId(ws.id)
    setEditPath(ws.remote_path)
    setEditLabel(ws.label)
  }

  const saveEdit = () => {
    if (!editingId) return
    updateWorkspaceMutation.mutate({
      id: editingId,
      remote_path: editPath,
      label: editLabel,
    })
  }

  // Filter workspaces for selected agent
  const agentWorkspaces = selectedAgent
    ? workspaces.filter((w) => w.agent_id === selectedAgent.id)
    : workspaces

  // Projects not yet assigned to any workspace
  const availableProjects = projects.filter(
    (p) => !workspaces.some((w) => w.project_id === p.id)
  )

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar — Agent list */}
      <div className="w-64 flex-shrink-0 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-gray-700">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
            <h2 className="font-semibold text-sm text-gray-800 dark:text-gray-100">Agents</h2>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => qc.invalidateQueries({ queryKey: ['workspace-agents'] })}
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
              title="更新"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setShowRegister(true)}
              className="p-1.5 rounded-lg text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/30"
              title="Agent 登録"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {agentsLoading ? (
            <div className="text-center py-8 text-gray-400 text-sm">読み込み中...</div>
          ) : (
            <>
              {/* "All" option */}
              <div
                className={`flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors mb-1 ${
                  !selectedAgent
                    ? 'bg-indigo-50 dark:bg-indigo-900/30 border border-indigo-200 dark:border-indigo-800'
                    : 'hover:bg-gray-100 dark:hover:bg-gray-700 border border-transparent'
                }`}
                onClick={() => setSelectedAgent(null)}
              >
                <span className="text-sm font-medium text-gray-800 dark:text-gray-200">すべて</span>
              </div>
              <AgentList
                agents={agents}
                selectedAgentId={selectedAgent?.id ?? null}
                onSelect={handleSelectAgent}
                onDelete={handleDeleteAgent}
              />
            </>
          )}
        </div>
      </div>

      {/* Main — Workspace list */}
      <div className="flex-1 flex flex-col min-w-0 bg-gray-50 dark:bg-gray-900">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <h1 className="text-lg font-semibold text-gray-800 dark:text-gray-100">
            ワークスペース
            {selectedAgent && (
              <span className="ml-2 text-sm font-normal text-gray-500">— {selectedAgent.name}</span>
            )}
          </h1>
          <button
            onClick={() => setShowCreate(true)}
            disabled={availableProjects.length === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Plus className="w-4 h-4" />
            追加
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {agentWorkspaces.length === 0 ? (
            <div className="text-center py-16 text-gray-400 dark:text-gray-500">
              <FolderOpen className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">ワークスペースがありません</p>
              <p className="text-xs mt-1">「追加」からプロジェクトをリモートディレクトリに紐づけてください</p>
            </div>
          ) : (
            <div className="space-y-3">
              {agentWorkspaces.map((ws) => (
                <div
                  key={ws.id}
                  className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 px-5 py-4"
                >
                  {editingId === ws.id ? (
                    <div className="space-y-3">
                      <div>
                        <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">リモートパス</label>
                        <input
                          value={editPath}
                          onChange={(e) => setEditPath(e.target.value)}
                          className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 focus:ring-2 focus:ring-indigo-500"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">ラベル</label>
                        <input
                          value={editLabel}
                          onChange={(e) => setEditLabel(e.target.value)}
                          className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 focus:ring-2 focus:ring-indigo-500"
                        />
                      </div>
                      <div className="flex justify-end gap-2">
                        <button onClick={() => setEditingId(null)} className="px-3 py-1 text-sm text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg">キャンセル</button>
                        <button onClick={saveEdit} className="px-3 py-1 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">保存</button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start justify-between">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-gray-800 dark:text-gray-100">{ws.project_name}</span>
                          {ws.label && (
                            <span className="text-xs text-gray-400 dark:text-gray-500">({ws.label})</span>
                          )}
                          <span className={`inline-flex items-center px-1.5 py-0.5 text-xs rounded-full ${
                            ws.is_online
                              ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                              : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
                          }`}>
                            {ws.is_online ? 'online' : 'offline'}
                          </span>
                        </div>
                        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5 font-mono truncate">
                          {ws.remote_path}
                        </p>
                        <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                          Agent: {ws.agent_name}
                        </p>
                      </div>
                      <div className="flex items-center gap-1 flex-shrink-0 ml-4">
                        <button
                          onClick={() => startEdit(ws)}
                          className="p-1.5 rounded-lg text-gray-400 hover:text-indigo-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                          title="編集"
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => handleDeleteWorkspace(ws.id)}
                          className="p-1.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                          title="削除"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Dialogs */}
      <AgentRegisterDialog
        open={showRegister}
        onClose={() => setShowRegister(false)}
        onCreated={() => qc.invalidateQueries({ queryKey: ['workspace-agents'] })}
      />

      {showCreate && (
        <WorkspaceCreateDialog
          agents={agents}
          projects={availableProjects}
          defaultAgentId={selectedAgent?.id ?? ''}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            qc.invalidateQueries({ queryKey: ['workspaces'] })
            setShowCreate(false)
          }}
        />
      )}
    </div>
  )
}


// ── Create Dialog ────────────────────────────────────────────

function WorkspaceCreateDialog({
  agents,
  projects,
  defaultAgentId,
  onClose,
  onCreated,
}: {
  agents: Agent[]
  projects: Project[]
  defaultAgentId: string
  onClose: () => void
  onCreated: () => void
}) {
  const [agentId, setAgentId] = useState(defaultAgentId || agents[0]?.id || '')
  const [projectId, setProjectId] = useState(projects[0]?.id || '')
  const [remotePath, setRemotePath] = useState('')
  const [label, setLabel] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!agentId || !projectId || !remotePath.trim()) return

    setLoading(true)
    setError('')
    try {
      await api.post('/workspaces', {
        agent_id: agentId,
        project_id: projectId,
        remote_path: remotePath.trim(),
        label: label.trim(),
      })
      onCreated()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'ワークスペースの作成に失敗しました')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-md mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100">ワークスペース追加</h3>
        </div>
        <form onSubmit={handleSubmit} className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Agent</label>
            <select
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200"
            >
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name} ({a.hostname || 'unknown'})</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">プロジェクト</label>
            <select
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200"
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              リモートパス <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={remotePath}
              onChange={(e) => setRemotePath(e.target.value)}
              placeholder="/home/user/projects/my-project"
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 font-mono focus:ring-2 focus:ring-indigo-500"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">ラベル（任意）</label>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="例: production server"
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg">
              キャンセル
            </button>
            <button
              type="submit"
              disabled={loading || !agentId || !projectId || !remotePath.trim()}
              className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {loading ? '作成中...' : '作成'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
