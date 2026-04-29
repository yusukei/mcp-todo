import { useCallback, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Bot,
  Check,
  ExternalLink,
  FolderOpen,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  ServerCog,
  Trash2,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react'
import { api } from '../api/client'
import AgentList, { type Agent } from '../components/workspace/AgentList'
import AgentRegisterDialog from '../components/workspace/AgentRegisterDialog'
import { showErrorToast, showSuccessToast } from '../components/common/Toast'
import { showConfirm } from '../components/common/ConfirmDialog'

type Channel = 'stable' | 'beta' | 'canary'

interface Supervisor {
  id: string
  name: string
  host_id: string
  hostname: string
  os_type: string
  is_online: boolean
  supervisor_version: string | null
  agent_version: string | null
  agent_pid: number | null
  agent_uptime_s: number | null
  joined_agent_id: string | null
  last_seen_at: string | null
  created_at: string
}

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

type Selection = { type: 'agent'; id: string } | { type: 'supervisor'; id: string } | null

const CHANNELS: Channel[] = ['stable', 'beta', 'canary']

function osLabel(osType: string): string {
  if (osType === 'darwin') return 'macOS'
  if (osType === 'win32') return 'Windows'
  if (osType === 'linux') return 'Linux'
  return osType || 'Unknown'
}

function versionLabel(version: string | null | undefined): string {
  return version ? `v${version}` : 'unknown'
}

function formatUptime(seconds: number | null): string {
  if (!seconds) return '-'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function StatusPill({ online }: { online: boolean }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
        online
          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
          : 'bg-gray-200 text-gray-500 dark:bg-gray-700 dark:text-gray-400'
      }`}
    >
      {online ? 'online' : 'offline'}
    </span>
  )
}

function VersionBox({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800">
      <div className="text-[11px] uppercase tracking-wide text-gray-400">{label}</div>
      <div className="mt-1 font-mono text-sm text-gray-900 dark:text-gray-100">
        {value || 'unknown'}
      </div>
    </div>
  )
}

interface EditableNameProps {
  initialName: string
  disabled?: boolean
  onSave: (name: string) => void
}

function EditableName({ initialName, disabled, onSave }: EditableNameProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(initialName)
  if (!editing) {
    return (
      <div className="flex items-center gap-2 min-w-0">
        <h1 className="truncate text-xl font-semibold text-gray-900 dark:text-gray-50">
          {initialName}
        </h1>
        <button
          onClick={() => {
            setDraft(initialName)
            setEditing(true)
          }}
          disabled={disabled}
          className="rounded p-1 text-gray-400 hover:bg-gray-200 hover:text-gray-700 disabled:opacity-50 dark:hover:bg-gray-700 dark:hover:text-gray-100"
          title="名前を編集"
        >
          <Pencil className="h-4 w-4" />
        </button>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-2 min-w-0">
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        className="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-accent-400 focus:outline-none focus:ring-2 focus:ring-focus dark:border-gray-700 dark:bg-gray-900 dark:text-gray-50"
        autoFocus
      />
      <button
        onClick={() => {
          const next = draft.trim()
          if (next) onSave(next)
          setEditing(false)
        }}
        disabled={disabled || !draft.trim()}
        className="rounded p-2 text-accent-600 hover:bg-accent-50 disabled:opacity-50 dark:text-accent-400 dark:hover:bg-accent-900/30"
        title="保存"
      >
        <Check className="h-4 w-4" />
      </button>
      <button
        onClick={() => setEditing(false)}
        className="rounded p-2 text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
        title="キャンセル"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}

export default function WorkspacePage() {
  const qc = useQueryClient()
  const [selection, setSelection] = useState<Selection>(null)
  const [showRegister, setShowRegister] = useState(false)

  const { data: agents = [], isLoading: agentsLoading } = useQuery<Agent[]>({
    queryKey: ['workspace-agents'],
    queryFn: () => api.get('/workspaces/agents').then((r) => r.data),
    refetchInterval: 10000,
  })

  const { data: supervisors = [], isLoading: supervisorsLoading } = useQuery<Supervisor[]>({
    queryKey: ['workspace-supervisors'],
    queryFn: () => api.get('/workspaces/supervisors').then((r) => r.data),
    refetchInterval: 10000,
  })

  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects', 'include-hidden'],
    queryFn: () =>
      api.get('/projects', { params: { include_hidden: true } }).then((r) => r.data),
  })

  const selectedAgent = selection?.type === 'agent'
    ? agents.find((a) => a.id === selection.id) ?? null
    : null
  const selectedSupervisor = selection?.type === 'supervisor'
    ? supervisors.find((s) => s.id === selection.id) ?? null
    : null

  const boundProjects = useMemo(() => {
    const agentId =
      selectedAgent?.id ??
      selectedSupervisor?.joined_agent_id ??
      null
    return agentId
      ? projects.filter((p) => p.remote?.agent_id === agentId)
      : projects.filter((p) => p.remote != null)
  }, [projects, selectedAgent, selectedSupervisor])

  const refreshAll = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['workspace-agents'] })
    qc.invalidateQueries({ queryKey: ['workspace-supervisors'] })
  }, [qc])

  const deleteAgent = useMutation({
    mutationFn: (agentId: string) => api.delete(`/workspaces/agents/${agentId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspace-agents'] })
      showSuccessToast('Agent を削除しました')
    },
    onError: () => showErrorToast('Agent の削除に失敗しました'),
  })

  const deleteSupervisor = useMutation({
    mutationFn: (supervisorId: string) => api.delete(`/workspaces/supervisors/${supervisorId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspace-supervisors'] })
      showSuccessToast('Supervisor を削除しました')
    },
    onError: () => showErrorToast('Supervisor の削除に失敗しました'),
  })

  const updateAgent = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Agent> }) =>
      api.patch(`/workspaces/agents/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspace-agents'] })
      showSuccessToast('Agent を更新しました')
    },
    onError: () => showErrorToast('Agent の更新に失敗しました'),
  })

  const updateSupervisor = useMutation({
    mutationFn: ({ id, body }: { id: string; body: { name?: string } }) =>
      api.patch(`/workspaces/supervisors/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workspace-supervisors'] })
      showSuccessToast('Supervisor を更新しました')
    },
    onError: () => showErrorToast('Supervisor の更新に失敗しました'),
  })

  const checkAgentUpdate = useMutation({
    mutationFn: (agentId: string) => api.post(`/workspaces/agents/${agentId}/check-update`),
    onSuccess: (r) => {
      const body = r.data
      showSuccessToast(body.pushed ? `Agent ${versionLabel(body.version)} の更新を通知しました` : `更新なし: ${body.reason}`)
      qc.invalidateQueries({ queryKey: ['workspace-agents'] })
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showErrorToast(`Agent 更新チェックに失敗しました${detail ? `: ${detail}` : ''}`)
    },
  })

  const checkSupervisorUpdate = useMutation({
    mutationFn: ({ id, channel }: { id: string; channel: Channel }) =>
      api.post(`/workspaces/supervisors/${id}/check-update`, null, { params: { channel } }),
    onSuccess: (r) => {
      const body = r.data
      showSuccessToast(body.pushed ? `Supervisor ${versionLabel(body.version)} の更新を実行しました` : `更新なし: ${body.reason}`)
      qc.invalidateQueries({ queryKey: ['workspace-supervisors'] })
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showErrorToast(`Supervisor 更新チェックに失敗しました${detail ? `: ${detail}` : ''}`)
    },
  })

  const handleDeleteAgent = useCallback(
    async (agentId: string) => {
      if (await showConfirm('この Agent を削除しますか？')) {
        deleteAgent.mutate(agentId)
        if (selection?.type === 'agent' && selection.id === agentId) setSelection(null)
      }
    },
    [deleteAgent, selection],
  )

  const handleDeleteSupervisor = useCallback(
    async (supervisorId: string) => {
      if (await showConfirm('この Supervisor を削除しますか？')) {
        deleteSupervisor.mutate(supervisorId)
        if (selection?.type === 'supervisor' && selection.id === supervisorId) setSelection(null)
      }
    },
    [deleteSupervisor, selection],
  )

  return (
    <div className="flex h-full overflow-hidden">
      <aside className="w-[360px] flex-shrink-0 border-r border-gray-200 bg-gray-100 dark:border-gray-700 dark:bg-gray-800">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-gray-700">
          <div>
            <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-50">
              リモート実行環境
            </h2>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Supervisor / Agent の状態とバージョン
            </p>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={refreshAll}
              className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
              title="更新"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
            <button
              onClick={() => setShowRegister(true)}
              className="rounded-lg p-1.5 text-accent-600 hover:bg-accent-50 dark:text-accent-400 dark:hover:bg-accent-900/30"
              title="Agent 登録"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="h-full overflow-y-auto p-3 pb-20">
          <button
            onClick={() => setSelection(null)}
            className={`mb-3 flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
              !selection
                ? 'border-accent-300 bg-accent-50 text-gray-900 dark:border-accent-800 dark:bg-accent-900/30 dark:text-gray-100'
                : 'border-transparent text-gray-700 hover:bg-gray-200 dark:text-gray-200 dark:hover:bg-gray-700'
            }`}
          >
            <span className="font-medium">すべて</span>
            <span className="text-xs text-gray-500">{supervisors.length + agents.length} hosts</span>
          </button>

          <section className="mb-5">
            <div className="mb-2 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              <ServerCog className="h-3.5 w-3.5" />
              Supervisors
            </div>
            {supervisorsLoading ? (
              <div className="py-6 text-center text-sm text-gray-400">読み込み中...</div>
            ) : supervisors.length === 0 ? (
              <div className="rounded-lg border border-dashed border-gray-300 px-3 py-5 text-center text-sm text-gray-400 dark:border-gray-700">
                Supervisor が登録されていません
              </div>
            ) : (
              <div className="space-y-1">
                {supervisors.map((s) => (
                  <div
                    key={s.id}
                    onClick={() => setSelection({ type: 'supervisor', id: s.id })}
                    className={`group flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-3 transition-colors ${
                      selection?.type === 'supervisor' && selection.id === s.id
                        ? 'border-accent-300 bg-accent-50 dark:border-accent-800 dark:bg-accent-900/30'
                        : 'border-transparent hover:bg-gray-200 dark:hover:bg-gray-700'
                    }`}
                  >
                    <div className="pt-0.5">
                      {s.is_online ? (
                        <Wifi className="h-4 w-4 text-green-500" />
                      ) : (
                        <WifiOff className="h-4 w-4 text-gray-400" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                        {s.name}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        <span className="text-xs text-gray-400">{osLabel(s.os_type)}</span>
                        <StatusPill online={s.is_online} />
                        <span className="rounded bg-gray-200 px-1.5 py-0.5 font-mono text-[10px] text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                          sv {versionLabel(s.supervisor_version)}
                        </span>
                        <span className="rounded bg-gray-200 px-1.5 py-0.5 font-mono text-[10px] text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                          ag {versionLabel(s.agent_version)}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-xs text-gray-400">
                        {s.hostname || 'hostname 未取得'}
                      </p>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        handleDeleteSupervisor(s.id)
                      }}
                      className="rounded p-1 text-gray-300 opacity-0 transition-opacity hover:text-red-500 group-hover:opacity-100 dark:text-gray-600 dark:hover:text-red-400"
                      title="Supervisor を削除"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <div className="mb-2 flex items-center gap-2 px-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
              <Bot className="h-3.5 w-3.5" />
              Agents
            </div>
            {agentsLoading ? (
              <div className="py-6 text-center text-sm text-gray-400">読み込み中...</div>
            ) : (
              <AgentList
                agents={agents}
                selectedAgentId={selection?.type === 'agent' ? selection.id : null}
                onSelect={(agent) => setSelection({ type: 'agent', id: agent.id })}
                onDelete={handleDeleteAgent}
              />
            )}
          </section>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col bg-gray-50 dark:bg-gray-900">
        <div className="border-b border-gray-200 bg-white px-6 py-5 dark:border-gray-700 dark:bg-gray-800">
          {selectedAgent ? (
            <AgentDetail
              key={selectedAgent.id}
              agent={selectedAgent}
              saving={updateAgent.isPending}
              checking={checkAgentUpdate.isPending}
              onSave={(body) => updateAgent.mutate({ id: selectedAgent.id, body })}
              onCheck={() => checkAgentUpdate.mutate(selectedAgent.id)}
            />
          ) : selectedSupervisor ? (
            <SupervisorDetail
              key={selectedSupervisor.id}
              supervisor={selectedSupervisor}
              saving={updateSupervisor.isPending}
              checking={checkSupervisorUpdate.isPending}
              onSave={(body) => updateSupervisor.mutate({ id: selectedSupervisor.id, body })}
              onCheck={(channel) => checkSupervisorUpdate.mutate({ id: selectedSupervisor.id, channel })}
              joinedAgent={agents.find((a) => a.id === selectedSupervisor.joined_agent_id) ?? null}
            />
          ) : (
            <div>
              <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-50">
                リモート実行環境
              </h1>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                左の一覧から Supervisor または Agent を選択すると、名前・バージョン・更新状態を確認できます。
              </p>
              <div className="mt-4 grid max-w-2xl grid-cols-2 gap-3">
                <VersionBox label="Supervisors" value={String(supervisors.length)} />
                <VersionBox label="Agents" value={String(agents.length)} />
              </div>
            </div>
          )}
        </div>

        <section className="min-h-0 flex-1 overflow-y-auto p-6">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                バインド済みプロジェクト
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                優先度低めの参照情報です。編集はプロジェクト設定から行います。
              </p>
            </div>
          </div>

          {boundProjects.length === 0 ? (
            <div className="py-14 text-center text-gray-400 dark:text-gray-500">
              <FolderOpen className="mx-auto mb-3 h-10 w-10 opacity-30" />
              <p className="text-sm">バインドされたプロジェクトがありません</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
              {boundProjects.map((p) => {
                const agent = agents.find((a) => a.id === p.remote?.agent_id)
                return (
                  <div
                    key={p.id}
                    className="rounded-lg border border-gray-200 bg-white px-4 py-3 dark:border-gray-700 dark:bg-gray-800"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-gray-900 dark:text-gray-100">
                            {p.name}
                          </span>
                          {p.hidden && (
                            <span className="text-xs text-gray-400 dark:text-gray-500">(共通)</span>
                          )}
                          {agent && <StatusPill online={agent.is_online} />}
                        </div>
                        <p className="mt-1 truncate font-mono text-sm text-gray-500 dark:text-gray-400">
                          {p.remote?.remote_path}
                        </p>
                        <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">
                          Agent: {agent?.name ?? '(不明)'}
                          {p.remote?.label ? ` / ${p.remote.label}` : ''}
                        </p>
                      </div>
                      <Link
                        to={`/projects/${p.id}/settings`}
                        className="flex flex-shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-xs text-accent-600 hover:bg-accent-50 dark:text-accent-400 dark:hover:bg-accent-900/30"
                        title="プロジェクト設定で編集"
                      >
                        設定
                        <ExternalLink className="h-3 w-3" />
                      </Link>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>
      </main>

      <AgentRegisterDialog
        open={showRegister}
        onClose={() => setShowRegister(false)}
        onCreated={refreshAll}
      />
    </div>
  )
}

interface AgentDetailProps {
  agent: Agent
  saving: boolean
  checking: boolean
  onSave: (body: Partial<Agent>) => void
  onCheck: () => void
}

function AgentDetail({ agent, saving, checking, onSave, onCheck }: AgentDetailProps) {
  const [autoUpdate, setAutoUpdate] = useState(agent.auto_update ?? true)
  const [channel, setChannel] = useState<Channel>(agent.update_channel ?? 'stable')
  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-2 flex items-center gap-2">
            <Bot className="h-5 w-5 text-accent-500" />
            <EditableName
              initialName={agent.name}
              disabled={saving}
              onSave={(name) => onSave({ name })}
            />
            <StatusPill online={agent.is_online} />
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {agent.hostname || 'hostname 未取得'} / {osLabel(agent.os_type)}
          </p>
        </div>
        <button
          onClick={onCheck}
          disabled={!agent.is_online || checking}
          className="inline-flex items-center gap-2 rounded-lg bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${checking ? 'animate-spin' : ''}`} />
          更新チェック
        </button>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
        <VersionBox label="Agent version" value={agent.agent_version} />
        <VersionBox label="Update channel" value={channel} />
        <VersionBox label="Last seen" value={agent.last_seen_at ? new Date(agent.last_seen_at).toLocaleString('ja-JP') : null} />
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
          <input
            type="checkbox"
            checked={autoUpdate}
            onChange={(e) => setAutoUpdate(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 text-accent-600 focus:ring-accent-500"
          />
          自動更新
        </label>
        <select
          value={channel}
          onChange={(e) => setChannel(e.target.value as Channel)}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
        >
          {CHANNELS.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <button
          onClick={() => onSave({ auto_update: autoUpdate, update_channel: channel })}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-700 dark:text-gray-100 dark:hover:bg-gray-700"
        >
          <Save className="h-4 w-4" />
          設定を保存
        </button>
      </div>
    </div>
  )
}

interface SupervisorDetailProps {
  supervisor: Supervisor
  joinedAgent: Agent | null
  saving: boolean
  checking: boolean
  onSave: (body: { name?: string }) => void
  onCheck: (channel: Channel) => void
}

function SupervisorDetail({
  supervisor,
  joinedAgent,
  saving,
  checking,
  onSave,
  onCheck,
}: SupervisorDetailProps) {
  const [channel, setChannel] = useState<Channel>('stable')
  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-2 flex items-center gap-2">
            <ServerCog className="h-5 w-5 text-accent-500" />
            <EditableName
              initialName={supervisor.name}
              disabled={saving}
              onSave={(name) => onSave({ name })}
            />
            <StatusPill online={supervisor.is_online} />
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {supervisor.hostname || 'hostname 未取得'} / {osLabel(supervisor.os_type)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={channel}
            onChange={(e) => setChannel(e.target.value as Channel)}
            className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100"
          >
            {CHANNELS.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <button
            onClick={() => onCheck(channel)}
            disabled={!supervisor.is_online || checking}
            className="inline-flex items-center gap-2 rounded-lg bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${checking ? 'animate-spin' : ''}`} />
            Supervisor 更新
          </button>
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-4">
        <VersionBox label="Supervisor version" value={supervisor.supervisor_version} />
        <VersionBox label="Managed agent" value={supervisor.agent_version} />
        <VersionBox label="Agent PID" value={supervisor.agent_pid ? String(supervisor.agent_pid) : null} />
        <VersionBox label="Uptime" value={formatUptime(supervisor.agent_uptime_s)} />
      </div>
      <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600 dark:border-gray-700 dark:bg-gray-900/60 dark:text-gray-300">
        ペア Agent: {joinedAgent ? `${joinedAgent.name} (${versionLabel(joinedAgent.agent_version)})` : '未接続または未登録'}
      </div>
    </div>
  )
}
