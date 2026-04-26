import { useCallback } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, List as ListIcon } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import TerminalView from '../components/workspace/TerminalView'
import TerminalSessionList from '../components/workspace/TerminalSessionList'
import type { Agent } from '../components/workspace/AgentList'

/**
 * URL routing (Phase A):
 *
 *   /workspaces/terminal/:agentId               → session list
 *   /workspaces/terminal/:agentId/new           → create a new session;
 *                                                 ``onSessionStarted``
 *                                                 rewrites the URL once
 *                                                 the backend assigns id.
 *   /workspaces/terminal/:agentId/:sessionId    → attach to an existing
 *                                                 session (replays scrollback).
 *
 * A browser reload preserves :sessionId, so the same agent-side PTY
 * is reattached and the screen is restored from the agent's
 * scrollback buffer.
 */
export default function TerminalPage() {
  const { agentId, sessionId } = useParams<{
    agentId: string
    sessionId?: string
  }>()
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

  const handleSessionStarted = useCallback(
    (newSessionId: string) => {
      // Persist the agent-assigned id in the URL so a reload
      // reattaches to the same session instead of spawning a new
      // one. ``replace: true`` because the previous URL ("/new")
      // is a transient intent, not history-worthy.
      navigate(`/workspaces/terminal/${agentId}/${newSessionId}`, {
        replace: true,
      })
    },
    [agentId, navigate],
  )

  if (!agentId) {
    return <div className="p-8 text-gray-400">Invalid agent id.</div>
  }

  // ``new`` is a sentinel meaning "create a fresh session"; we pass
  // ``sessionId={undefined}`` to TerminalView, which opens the WS
  // without ``session_id`` so the backend allocates one.
  const isCreate = !sessionId || sessionId === 'new'
  const isList = !sessionId

  return (
    <div className="flex flex-col h-full bg-gray-900">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-gray-700">
        <Link
          to="/admin"
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          管理者設定
        </Link>
        <span className="text-gray-500">/</span>
        <Link
          to={`/workspaces/terminal/${agentId}`}
          className="text-sm text-gray-200 hover:text-emerald-400"
        >
          Terminal — {agent?.name ?? agentId}
        </Link>
        {sessionId && sessionId !== 'new' && (
          <>
            <span className="text-gray-500">/</span>
            <span className="text-xs text-gray-400 font-mono">
              {sessionId.slice(0, 12)}…
            </span>
          </>
        )}
        {!isList && (
          <Link
            to={`/workspaces/terminal/${agentId}`}
            className="ml-auto flex items-center gap-1 text-xs text-gray-400 hover:text-gray-200"
            title="Back to session list"
          >
            <ListIcon className="w-3.5 h-3.5" />
            Sessions
          </Link>
        )}
        <button
          onClick={() => navigate('/admin')}
          className={`text-xs text-gray-400 hover:text-gray-200 ${
            isList ? 'ml-auto' : ''
          }`}
        >
          Close
        </button>
      </div>
      <div className="flex-1 min-h-0">
        {isList ? (
          <TerminalSessionList agentId={agentId} />
        ) : (
          <TerminalView
            key={isCreate ? 'create' : sessionId}
            agentId={agentId}
            agentName={agent?.name}
            sessionId={isCreate ? undefined : sessionId}
            onSessionStarted={isCreate ? handleSessionStarted : undefined}
            onDisconnect={handleDisconnect}
          />
        )}
      </div>
    </div>
  )
}
