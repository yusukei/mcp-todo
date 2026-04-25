import { useCallback } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import TerminalView from '../components/workspace/TerminalView'
import type { Agent } from '../components/workspace/AgentList'

export default function TerminalPage() {
  const { agentId } = useParams<{ agentId: string }>()
  const navigate = useNavigate()

  const { data: agents = [] } = useQuery<Agent[]>({
    queryKey: ['workspace-agents'],
    queryFn: () => api.get('/workspaces/agents').then((r) => r.data),
  })

  const agent = agents.find((a) => a.id === agentId)

  const handleDisconnect = useCallback(
    (reason: string) => {
      console.info('[TerminalPage] disconnected:', reason)
    },
    [],
  )

  if (!agentId) {
    return <div className="p-8 text-gray-400">Invalid agent id.</div>
  }

  return (
    <div className="flex flex-col h-full bg-gray-900">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-gray-700">
        <Link
          to="/workspaces"
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Workspaces
        </Link>
        <span className="text-gray-500">/</span>
        <span className="text-sm text-gray-200">
          Terminal — {agent?.name ?? agentId}
        </span>
        <button
          onClick={() => navigate('/workspaces')}
          className="ml-auto text-xs text-gray-400 hover:text-gray-200"
        >
          Close
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <TerminalView
          agentId={agentId}
          agentName={agent?.name}
          onDisconnect={handleDisconnect}
        />
      </div>
    </div>
  )
}
