import { Monitor, Trash2, Wifi, WifiOff } from 'lucide-react'

interface Agent {
  id: string
  name: string
  hostname: string
  os_type: string
  available_shells: string[]
  is_online: boolean
  last_seen_at: string | null
  created_at: string
  agent_version: string | null
}

interface AgentListProps {
  agents: Agent[]
  selectedAgentId: string | null
  onSelect: (agent: Agent) => void
  onDelete: (agentId: string) => void
}

function osLabel(osType: string): string {
  if (osType === 'darwin') return 'macOS'
  if (osType === 'win32') return 'Windows'
  if (osType === 'linux') return 'Linux'
  return osType || 'Unknown'
}

export type { Agent }

export default function AgentList({ agents, selectedAgentId, onSelect, onDelete }: AgentListProps) {
  if (agents.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400 dark:text-gray-500 text-sm">
        <Monitor className="w-8 h-8 mx-auto mb-2 opacity-50" />
        <p>Agent が登録されていません</p>
        <p className="text-xs mt-1">右上のボタンから登録してください</p>
      </div>
    )
  }

  return (
    <div className="space-y-1">
      {agents.map((agent) => (
        <div
          key={agent.id}
          className={`group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ${
            selectedAgentId === agent.id
              ? 'bg-terracotta-50 dark:bg-terracotta-900/30 border border-terracotta-200 dark:border-terracotta-800'
              : 'hover:bg-gray-100 dark:hover:bg-gray-700 border border-transparent'
          }`}
          onClick={() => agent.is_online && onSelect(agent)}
        >
          <div className="flex-shrink-0">
            {agent.is_online ? (
              <Wifi className="w-4 h-4 text-green-500" />
            ) : (
              <WifiOff className="w-4 h-4 text-gray-400" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className={`text-sm font-medium truncate ${
                agent.is_online ? 'text-gray-800 dark:text-gray-200' : 'text-gray-400 dark:text-gray-500'
              }`}>
                {agent.name}
              </span>
              <span className="text-xs text-gray-400 dark:text-gray-500">
                {osLabel(agent.os_type)}
              </span>
              {agent.agent_version && (
                <span className="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 font-mono text-[10px]">
                  v{agent.agent_version}
                </span>
              )}
            </div>
            {agent.hostname && (
              <p className="text-xs text-gray-400 dark:text-gray-500 truncate">
                {agent.hostname}
              </p>
            )}
          </div>
          <button
            onClick={(e) => {
              e.stopPropagation()
              onDelete(agent.id)
            }}
            className="flex-shrink-0 p-1 rounded text-gray-300 dark:text-gray-600 opacity-0 group-hover:opacity-100 hover:text-red-500 dark:hover:text-red-400 transition-opacity"
            title="Agent を削除"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
    </div>
  )
}
