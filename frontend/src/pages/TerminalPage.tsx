import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, TerminalSquare, RefreshCw } from 'lucide-react'
import { api } from '../api/client'
import AgentList, { type Agent } from '../components/terminal/AgentList'
import AgentRegisterDialog from '../components/terminal/AgentRegisterDialog'
import TerminalView from '../components/terminal/TerminalView'

export default function TerminalPage() {
  const qc = useQueryClient()
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null)
  const [showRegister, setShowRegister] = useState(false)
  const [sessionKey, setSessionKey] = useState(0) // Force remount terminal on reconnect

  const { data: agents = [], isLoading } = useQuery({
    queryKey: ['terminal-agents'],
    queryFn: () => api.get('/terminal/agents').then((r) => r.data),
    refetchInterval: 5000, // Poll agent status every 5s
  })

  const deleteMutation = useMutation({
    mutationFn: (agentId: string) => api.delete(`/terminal/agents/${agentId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['terminal-agents'] })
      if (selectedAgent && !agents.find((a: Agent) => a.id === selectedAgent.id)) {
        setSelectedAgent(null)
      }
    },
  })

  const handleSelect = useCallback((agent: Agent) => {
    setSelectedAgent(agent)
    setSessionKey((k) => k + 1)
  }, [])

  const handleDelete = useCallback((agentId: string) => {
    if (confirm('この Agent を削除しますか？')) {
      deleteMutation.mutate(agentId)
      if (selectedAgent?.id === agentId) {
        setSelectedAgent(null)
      }
    }
  }, [deleteMutation, selectedAgent])

  const handleDisconnect = useCallback(() => {
    // Keep agent selected but allow reconnect
  }, [])

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar: Agent list */}
      <div className="w-64 flex-shrink-0 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-gray-700">
          <div className="flex items-center gap-2">
            <TerminalSquare className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
            <h2 className="font-semibold text-sm text-gray-800 dark:text-gray-100">Agents</h2>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => qc.invalidateQueries({ queryKey: ['terminal-agents'] })}
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
          {isLoading ? (
            <div className="text-center py-8 text-gray-400 text-sm">読み込み中...</div>
          ) : (
            <AgentList
              agents={agents}
              selectedAgentId={selectedAgent?.id ?? null}
              onSelect={handleSelect}
              onDelete={handleDelete}
            />
          )}
        </div>
      </div>

      {/* Main: Terminal */}
      <div className="flex-1 flex flex-col min-w-0">
        {selectedAgent ? (
          <TerminalView
            key={`${selectedAgent.id}-${sessionKey}`}
            agentId={selectedAgent.id}
            agentName={selectedAgent.name}
            shell={selectedAgent.available_shells[0] || ''}
            onDisconnect={handleDisconnect}
          />
        ) : (
          <div className="flex-1 flex items-center justify-center bg-gray-50 dark:bg-gray-900">
            <div className="text-center text-gray-400 dark:text-gray-500">
              <TerminalSquare className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">
                {agents.length === 0
                  ? 'Agent を登録してリモートターミナルに接続'
                  : 'オンラインの Agent を選択して接続'}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Register dialog */}
      <AgentRegisterDialog
        open={showRegister}
        onClose={() => setShowRegister(false)}
        onCreated={() => qc.invalidateQueries({ queryKey: ['terminal-agents'] })}
      />
    </div>
  )
}
