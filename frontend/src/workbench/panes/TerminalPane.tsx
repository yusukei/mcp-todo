import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Server, ExternalLink } from 'lucide-react'
import { api } from '../../api/client'
import TerminalView from '../../components/workspace/TerminalView'
import type { TerminalViewHandle } from '../../components/workspace/TerminalView'
import type { PaneComponentProps } from '../paneRegistry'
import { useWorkbenchEvent } from '../eventBus'

interface ProjectWithRemote {
  id: string
  name: string
  remote: { agent_id: string; remote_path: string; label?: string } | null
}

interface AgentInfo {
  id: string
  name: string
  is_online: boolean
}

/**
 * Wrap TerminalView (Phase A) so it lives inside the Workbench
 * pane registry. The agent comes from ``Project.remote.agent_id``;
 * the session id is stored in ``paneConfig.sessionId`` so a reload
 * reattaches to the same PTY.
 *
 * Defensive behaviours:
 * - **Agent rebind invalidation** — the agentId at the time the
 *   pane was created is snapshotted into ``paneConfig.agentId``.
 *   When the project's binding changes, the snapshot won't match
 *   the live agentId and we drop the stale sessionId so the next
 *   mount creates a fresh session on the new agent.
 * - **Stale sessionId detection** — on mount we list the agent's
 *   sessions; if our stored sessionId isn't there (the agent was
 *   restarted, the session was killed elsewhere, etc.) we clear
 *   the config so a new session is spawned instead of looping in
 *   ``terminal_attach`` failures.
 */
export default function TerminalPane({
  paneId,
  projectId,
  paneConfig,
  onConfigChange,
}: PaneComponentProps) {
  const config = paneConfig as {
    sessionId?: string
    agentId?: string
  }

  // Imperative handle on TerminalView so we can inject ``cd <path>``
  // when this pane is the routing target for an ``open-terminal-cwd``
  // event from the file browser.
  const tvRef = useRef<TerminalViewHandle | null>(null)

  useWorkbenchEvent(paneId, 'open-terminal-cwd', ({ cwd }) => {
    // Quote the path so spaces / special chars don't break the cd.
    // Use double quotes + escape any embedded double quotes; this
    // matches the most common posix + Windows cmd quoting style. The
    // trailing newline submits the line.
    const safe = cwd.replace(/"/g, '\\"')
    tvRef.current?.sendInput(`cd "${safe}"\n`)
  })

  const { data: project, isLoading: projectLoading } = useQuery<ProjectWithRemote>({
    queryKey: ['project', projectId],
    queryFn: () => api.get(`/projects/${projectId}`).then((r) => r.data),
  })

  const liveAgentId = project?.remote?.agent_id ?? null

  const { data: agent } = useQuery<AgentInfo>({
    queryKey: ['workspace-agent', liveAgentId],
    queryFn: () =>
      api
        .get(`/workspaces/agents`)
        .then((r) =>
          (r.data as AgentInfo[]).find((a) => a.id === liveAgentId) ?? null,
        )
        .then((a: AgentInfo | null) => {
          if (!a) throw new Error('agent not found')
          return a
        }),
    enabled: !!liveAgentId,
  })

  // Probe the agent's session list and drop a stored sessionId that
  // no longer exists on the agent (e.g. supervisor restarted, kill
  // came from another tab). Without this we'd attempt
  // terminal_attach forever and the user would see only "session
  // not found" errors.
  const [sessionProbed, setSessionProbed] = useState(false)
  useEffect(() => {
    if (!liveAgentId || !config.sessionId || sessionProbed) return
    let cancelled = false
    api
      .get(`/workspaces/terminal/${liveAgentId}/sessions`)
      .then((r) => {
        if (cancelled) return
        const sessions = (r.data?.sessions ?? []) as Array<{ session_id: string }>
        const stillExists = sessions.some((s) => s.session_id === config.sessionId)
        if (!stillExists) {
          // Drop the sessionId so the TerminalView (re-keyed below)
          // opens with create-mode.
          onConfigChange({ sessionId: undefined, agentId: liveAgentId })
        }
        setSessionProbed(true)
      })
      .catch(() => {
        // Probe failure shouldn't block the terminal — let
        // TerminalView attempt the attach and surface its own error.
        setSessionProbed(true)
      })
    return () => {
      cancelled = true
    }
  }, [liveAgentId, config.sessionId, sessionProbed, onConfigChange])

  // Agent rebind: drop the stored sessionId because it belongs to a
  // different agent's PTY namespace.
  useEffect(() => {
    if (!liveAgentId) return
    if (config.agentId && config.agentId !== liveAgentId) {
      onConfigChange({ sessionId: undefined, agentId: liveAgentId })
    } else if (!config.agentId) {
      onConfigChange({ agentId: liveAgentId })
    }
  }, [liveAgentId, config.agentId, onConfigChange])

  const handleSessionStarted = useCallback(
    (sessionId: string) => {
      onConfigChange({ sessionId, agentId: liveAgentId ?? undefined })
    },
    [onConfigChange, liveAgentId],
  )

  const handleDisconnect = useCallback((reason: string) => {
    // Eaten — TerminalView already shows status.  No need to mutate
    // paneConfig (the session may still be alive on the agent).
    void reason
  }, [])

  // ── Render: guidance / loading / live terminal ────────────────

  if (projectLoading) {
    return (
      <div className="h-full flex items-center justify-center text-gray-400">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    )
  }

  if (!project?.remote?.agent_id) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 p-6 text-center">
        <Server className="w-8 h-8 text-gray-400" />
        <p className="text-sm text-gray-700 dark:text-gray-300 font-medium">
          このプロジェクトには agent が紐付いていません
        </p>
        <p className="text-xs text-gray-500 dark:text-gray-400 max-w-md">
          Terminal pane を使うには、プロジェクト設定からリモート agent をバインドしてください。
        </p>
        <Link
          to={`/projects/${projectId}/settings`}
          className="text-xs text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1"
        >
          <ExternalLink className="w-3 h-3" />
          プロジェクト設定を開く
        </Link>
      </div>
    )
  }

  // Re-key TerminalView on sessionId change so the WS is torn down
  // and reopened cleanly when the user attaches to a different
  // session. Without this the WS state survives across switches and
  // dispatches output to the wrong session.
  const tvKey = useMemo(
    () => `${liveAgentId}:${config.sessionId ?? 'new'}`,
    [liveAgentId, config.sessionId],
  )

  return (
    <TerminalView
      key={tvKey}
      ref={tvRef}
      agentId={liveAgentId!}
      agentName={agent?.name}
      sessionId={config.sessionId}
      onSessionStarted={handleSessionStarted}
      onDisconnect={handleDisconnect}
    />
  )
}
