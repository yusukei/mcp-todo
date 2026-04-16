import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Plus, Server, RefreshCw, FolderOpen, ExternalLink } from 'lucide-react'
import { api } from '../api/client'
import AgentList, { type Agent } from '../components/workspace/AgentList'
import AgentRegisterDialog from '../components/workspace/AgentRegisterDialog'
import { showErrorToast, showSuccessToast } from '../components/common/Toast'
import { showConfirm } from '../components/common/ConfirmDialog'

interface ProjectRemoteBinding {
  agent_id: string
  remote_path: string
  label: string
  updated_at: string
}

interface Project {
  id: string
  name: string
  hidden?: boolean
  remote: ProjectRemoteBinding | null
}

export default function WorkspacePage() {
  const qc = useQueryClient()
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null)
  const [showRegister, setShowRegister] = useState(false)

  // ── Queries ──────────────────────────────────────────
  const { data: agents = [], isLoading: agentsLoading } = useQuery<Agent[]>({
    queryKey: ['workspace-agents'],
    queryFn: () => api.get('/workspaces/agents').then((r) => r.data),
    refetchInterval: 10000,
  })

  // Projects with a remote binding. We ask for hidden projects too so
  // the singleton "Common" project shows up when it is bound.
  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects', 'include-hidden'],
    queryFn: () =>
      api.get('/projects', { params: { include_hidden: true } }).then((r) => r.data),
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

  // ── Handlers ─────────────────────────────────────────
  const handleSelectAgent = useCallback((agent: Agent) => {
    setSelectedAgent(agent)
  }, [])

  const handleDeleteAgent = useCallback(
    async (agentId: string) => {
      if (await showConfirm('この Agent を削除しますか？')) {
        deleteMutation.mutate(agentId)
        if (selectedAgent?.id === agentId) setSelectedAgent(null)
      }
    },
    [deleteMutation, selectedAgent],
  )

  // Projects bound to an agent. The project → agent binding lives in
  // ``Project.remote`` (see ProjectSettingsPage) — this page is now
  // read-only: to edit/clear a binding, open the project's settings.
  const boundProjects = selectedAgent
    ? projects.filter((p) => p.remote?.agent_id === selectedAgent.id)
    : projects.filter((p) => p.remote != null)

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar — Agent list */}
      <div className="w-64 flex-shrink-0 bg-gray-100 dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-gray-700">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-terracotta-600 dark:text-terracotta-400" />
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
              className="p-1.5 rounded-lg text-terracotta-600 dark:text-terracotta-400 hover:bg-terracotta-50 dark:hover:bg-terracotta-900/30"
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
                    ? 'bg-terracotta-50 dark:bg-terracotta-900/30 border border-terracotta-200 dark:border-terracotta-800'
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

      {/* Main — Bound projects (read-only) */}
      <div className="flex-1 flex flex-col min-w-0 bg-gray-50 dark:bg-gray-900">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800">
          <div>
            <h1 className="text-lg font-semibold text-gray-800 dark:text-gray-100">
              バインド済みプロジェクト
              {selectedAgent && (
                <span className="ml-2 text-sm font-normal text-gray-500">
                  — {selectedAgent.name}
                </span>
              )}
            </h1>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              バインドの編集はプロジェクト設定画面から行います
            </p>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {boundProjects.length === 0 ? (
            <div className="text-center py-16 text-gray-400 dark:text-gray-500">
              <FolderOpen className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">バインドされたプロジェクトがありません</p>
              <p className="text-xs mt-1">
                プロジェクトの設定画面からエージェントを紐付けてください
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {boundProjects.map((p) => {
                const agent = agents.find((a) => a.id === p.remote?.agent_id)
                return (
                  <div
                    key={p.id}
                    className="bg-gray-100 dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 px-5 py-4"
                  >
                    <div className="flex items-start justify-between">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-gray-800 dark:text-gray-100">
                            {p.name}
                            {p.hidden && (
                              <span className="text-xs text-gray-400 dark:text-gray-500 ml-1">
                                (共通)
                              </span>
                            )}
                          </span>
                          {p.remote?.label && (
                            <span className="text-xs text-gray-400 dark:text-gray-500">
                              ({p.remote.label})
                            </span>
                          )}
                          {agent && (
                            <span
                              className={`inline-flex items-center px-1.5 py-0.5 text-xs rounded-full ${
                                agent.is_online
                                  ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                                  : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
                              }`}
                            >
                              {agent.is_online ? 'online' : 'offline'}
                            </span>
                          )}
                        </div>
                        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5 font-mono truncate">
                          {p.remote?.remote_path}
                        </p>
                        <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                          Agent: {agent?.name ?? '(不明)'}
                        </p>
                      </div>
                      <Link
                        to={`/projects/${p.id}/settings`}
                        className="flex items-center gap-1 px-2 py-1 text-xs text-terracotta-600 dark:text-terracotta-400 hover:bg-terracotta-50 dark:hover:bg-terracotta-900/30 rounded-lg ml-4 flex-shrink-0"
                        title="プロジェクト設定で編集"
                      >
                        設定を開く
                        <ExternalLink className="w-3 h-3" />
                      </Link>
                    </div>
                  </div>
                )
              })}
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
    </div>
  )
}
