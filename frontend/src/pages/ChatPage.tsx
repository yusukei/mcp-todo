import { useState, useEffect, useRef, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, Send, Square, MessageSquare, Loader2, Bot, User as UserIcon, AlertTriangle, ChevronDown, ChevronRight, DollarSign, Clock } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api/client'
import { showErrorToast } from '../components/common/Toast'

// ── Types ─────────────────────────────────────────────

interface ChatSession {
  id: string
  project_id: string
  title: string
  claude_session_id: string | null
  status: 'idle' | 'busy'
  model: string
  created_at: string
  updated_at: string
}

interface ToolCall {
  tool_name: string
  input: Record<string, unknown>
  output: string | null
}

interface ChatMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  tool_calls: ToolCall[]
  cost_usd: number | null
  duration_ms: number | null
  status: 'streaming' | 'complete' | 'error'
  created_at: string
}

interface WsEvent {
  type: string
  [key: string]: unknown
}

// ── WebSocket Hook ────────────────────────────────────

function useChatWs(sessionId: string | null) {
  const [connected, setConnected] = useState(false)
  const [sessionStatus, setSessionStatus] = useState<'idle' | 'busy'>('idle')
  const [streamingText, setStreamingText] = useState('')
  const [streamingTools, setStreamingTools] = useState<{ tool: string; input: Record<string, unknown>; output?: string }[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>()

  const connect = useCallback(() => {
    if (!sessionId) return
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    // Cookie is sent automatically with same-origin WebSocket connections.
    const url = `${proto}//${location.host}/api/v1/chat/ws/${sessionId}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      // Load history via REST
      api.get(`/chat/sessions/${sessionId}/messages?limit=200`).then(r => {
        setMessages(r.data.items)
      })
    }

    ws.onmessage = (ev) => {
      try {
        const data: WsEvent = JSON.parse(ev.data)
        switch (data.type) {
          case 'status':
            setSessionStatus(data.session_status as 'idle' | 'busy')
            break
          case 'user_message':
            setMessages(prev => [...prev, data.message as ChatMessage])
            break
          case 'assistant_start':
            setStreamingText('')
            setStreamingTools([])
            break
          case 'text_delta':
            setStreamingText(prev => prev + (data.text as string))
            break
          case 'tool_use':
            setStreamingTools(prev => [...prev, { tool: data.tool as string, input: data.input as Record<string, unknown> }])
            break
          case 'tool_result':
            setStreamingTools(prev => {
              const copy = [...prev]
              // Find last tool without output
              for (let i = copy.length - 1; i >= 0; i--) {
                if (!copy[i].output) {
                  copy[i] = { ...copy[i], output: data.output as string }
                  break
                }
              }
              return copy
            })
            break
          case 'assistant_end': {
            // Finalize: reload messages from server to get persisted state
            api.get(`/chat/sessions/${sessionId}/messages?limit=200`).then(r => {
              setMessages(r.data.items)
            })
            setStreamingText('')
            setStreamingTools([])
            break
          }
          case 'error':
            showErrorToast(data.detail as string)
            // Reload messages to get error message
            api.get(`/chat/sessions/${sessionId}/messages?limit=200`).then(r => {
              setMessages(r.data.items)
            })
            setStreamingText('')
            setStreamingTools([])
            break
        }
      } catch {}
    }

    ws.onclose = () => {
      setConnected(false)
      // Auto-reconnect
      reconnectRef.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => ws.close()
  }, [sessionId])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  const sendMessage = useCallback((content: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'send_message', content }))
    }
  }, [])

  const cancel = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'cancel' }))
    }
  }, [])

  return { connected, sessionStatus, messages, streamingText, streamingTools, sendMessage, cancel }
}

// ── Tool Call Card ────────────────────���───────────────

function ToolCallCard({ tool }: { tool: { tool_name?: string; tool?: string; input: Record<string, unknown>; output?: string | null } }) {
  const [expanded, setExpanded] = useState(false)
  const name = tool.tool_name || tool.tool || 'Unknown'
  const output = tool.output

  return (
    <div className="my-2 border border-gray-200 dark:border-gray-600 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs bg-gray-50 dark:bg-gray-700/50 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
      >
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        <span className="font-mono font-medium text-indigo-600 dark:text-indigo-400">{name}</span>
        {tool.input.file_path != null ? (
          <span className="text-gray-400 truncate">{`${tool.input.file_path}`}</span>
        ) : null}
        {tool.input.command != null ? (
          <span className="text-gray-400 truncate font-mono">{`${tool.input.command}`.slice(0, 60)}</span>
        ) : null}
      </button>
      {expanded && (
        <div className="px-3 py-2 text-xs space-y-2">
          {Object.keys(tool.input).length > 0 && (
            <div>
              <p className="font-semibold text-gray-500 dark:text-gray-400 mb-1">Input</p>
              <pre className="bg-gray-100 dark:bg-gray-800 p-2 rounded overflow-x-auto whitespace-pre-wrap break-all">
                {JSON.stringify(tool.input, null, 2)}
              </pre>
            </div>
          )}
          {output && (
            <div>
              <p className="font-semibold text-gray-500 dark:text-gray-400 mb-1">Output</p>
              <pre className="bg-gray-100 dark:bg-gray-800 p-2 rounded overflow-x-auto whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
                {output.length > 3000 ? output.slice(0, 3000) + '\n...(truncated)' : output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Message Bubble ────────────────────────────────────

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'
  const isError = msg.status === 'error'

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div className={`flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center ${
        isUser ? 'bg-indigo-100 dark:bg-indigo-900' :
        isSystem ? 'bg-amber-100 dark:bg-amber-900' :
        'bg-emerald-100 dark:bg-emerald-900'
      }`}>
        {isUser ? <UserIcon className="w-4 h-4 text-indigo-600 dark:text-indigo-400" /> :
         isSystem ? <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400" /> :
         <Bot className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />}
      </div>
      <div className={`flex-1 min-w-0 ${isUser ? 'text-right' : ''}`}>
        <div className={`inline-block text-left max-w-full rounded-xl px-4 py-3 text-sm ${
          isUser ? 'bg-indigo-600 text-white' :
          isError ? 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-800' :
          isSystem ? 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300' :
          'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700'
        }`}>
          {isUser ? (
            <p className="whitespace-pre-wrap">{msg.content}</p>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none break-words">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
            </div>
          )}
          {msg.tool_calls.length > 0 && (
            <div className="mt-2">
              {msg.tool_calls.map((tc, i) => (
                <ToolCallCard key={i} tool={tc} />
              ))}
            </div>
          )}
        </div>
        {msg.role === 'assistant' && msg.status === 'complete' && (msg.cost_usd || msg.duration_ms) && (
          <div className="flex items-center gap-3 mt-1 text-[10px] text-gray-400">
            {msg.cost_usd != null && (
              <span className="flex items-center gap-0.5"><DollarSign className="w-3 h-3" />{msg.cost_usd.toFixed(4)}</span>
            )}
            {msg.duration_ms != null && (
              <span className="flex items-center gap-0.5"><Clock className="w-3 h-3" />{(msg.duration_ms / 1000).toFixed(1)}s</span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Streaming Bubble ──────────────────────────────────

function StreamingBubble({ text, tools }: { text: string; tools: { tool: string; input: Record<string, unknown>; output?: string }[] }) {
  if (!text && tools.length === 0) {
    return (
      <div className="flex gap-3">
        <div className="flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center bg-emerald-100 dark:bg-emerald-900">
          <Bot className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" />
          考え中...
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-3">
      <div className="flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center bg-emerald-100 dark:bg-emerald-900">
        <Bot className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="inline-block text-left max-w-full rounded-xl px-4 py-3 text-sm bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
          {tools.map((t, i) => (
            <ToolCallCard key={i} tool={t} />
          ))}
          {text && (
            <div className="prose prose-sm dark:prose-invert max-w-none break-words">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
            </div>
          )}
          <Loader2 className="w-3 h-3 animate-spin text-gray-400 mt-1 inline-block" />
        </div>
      </div>
    </div>
  )
}

// ── Session List ──────────────────────────────────────

function SessionList({ projectId, activeId, onSelect, onCreate }: {
  projectId: string
  activeId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
}) {
  const qc = useQueryClient()
  const { data: sessions = [] } = useQuery<ChatSession[]>({
    queryKey: ['chat-sessions', projectId],
    queryFn: () => api.get('/chat/sessions', { params: { project_id: projectId } }).then(r => r.data),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/chat/sessions/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['chat-sessions', projectId] }),
  })

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-3 border-b border-gray-200 dark:border-gray-700">
        <button
          onClick={onCreate}
          className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
        >
          <Plus className="w-4 h-4" />
          新規チャット
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-1">
        {sessions.map(s => (
          <div
            key={s.id}
            className={`group flex items-center rounded-lg cursor-pointer ${
              s.id === activeId ? 'bg-indigo-50 dark:bg-indigo-900/30' : 'hover:bg-gray-100 dark:hover:bg-gray-700'
            }`}
          >
            <button
              onClick={() => onSelect(s.id)}
              className="flex-1 flex items-center gap-2 px-3 py-2 text-left min-w-0"
            >
              <MessageSquare className={`w-4 h-4 flex-shrink-0 ${
                s.status === 'busy' ? 'text-amber-500' : 'text-gray-400'
              }`} />
              <div className="min-w-0">
                <p className="text-sm truncate text-gray-700 dark:text-gray-200">{s.title}</p>
                <p className="text-[10px] text-gray-400">{new Date(s.updated_at).toLocaleString('ja-JP', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</p>
              </div>
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); deleteMutation.mutate(s.id) }}
              className="p-1.5 mr-1 rounded opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 transition-opacity"
              aria-label="削除"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
        {sessions.length === 0 && (
          <p className="text-xs text-gray-400 text-center py-4">チャットセッションがありません</p>
        )}
      </div>
    </div>
  )
}

// ── Main Chat Page ────────────────────────────────────

export default function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const qc = useQueryClient()
  const sessionId = searchParams.get('session')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const [input, setInput] = useState('')

  // Get first project for now (could be a selector later)
  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get('/projects').then(r => r.data),
  })
  const projectId = projects[0]?.id

  const { connected, sessionStatus, messages, streamingText, streamingTools, sendMessage, cancel } = useChatWs(sessionId)

  const createMutation = useMutation({
    mutationFn: (pid: string) => api.post('/chat/sessions', { project_id: pid }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['chat-sessions'] })
      setSearchParams({ session: r.data.id })
    },
    onError: () => showErrorToast('��ッション作成に失敗しました'),
  })

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText])

  const handleSend = () => {
    const trimmed = input.trim()
    if (!trimmed || sessionStatus === 'busy') return
    sendMessage(trimmed)
    setInput('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      handleSend()
    }
  }

  if (!projectId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        プロジェクトがありません
      </div>
    )
  }

  return (
    <div className="flex h-full">
      {/* Session sidebar */}
      <div className="w-60 flex-shrink-0 border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hidden lg:flex flex-col">
        <SessionList
          projectId={projectId}
          activeId={sessionId}
          onSelect={(id) => setSearchParams({ session: id })}
          onCreate={() => createMutation.mutate(projectId)}
        />
      </div>

      {/* Chat area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
            <h1 className="text-sm font-semibold text-gray-800 dark:text-gray-100">
              Claude Code Chat
            </h1>
            {sessionStatus === 'busy' && (
              <span className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                <Loader2 className="w-3 h-3 animate-spin" /> 処理中
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {!connected && sessionId && (
              <span className="text-xs text-red-500">切断中...</span>
            )}
            {/* Mobile: create new session */}
            <button
              onClick={() => createMutation.mutate(projectId)}
              className="lg:hidden p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
              aria-label="新規チャット"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Messages */}
        {sessionId ? (
          <>
            <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
              {messages.map(msg => (
                <MessageBubble key={msg.id} msg={msg} />
              ))}
              {sessionStatus === 'busy' && (
                <StreamingBubble text={streamingText} tools={streamingTools} />
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
              <div className="flex items-end gap-2">
                <textarea
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={sessionStatus === 'busy' ? '応答待ち...' : 'メッセージを入力... (Ctrl+Enter で送信)'}
                  disabled={sessionStatus === 'busy'}
                  rows={Math.min(input.split('\n').length, 6)}
                  className="flex-1 resize-none rounded-xl border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 px-4 py-2.5 text-sm text-gray-800 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50"
                />
                {sessionStatus === 'busy' ? (
                  <button
                    onClick={cancel}
                    className="flex-shrink-0 p-2.5 rounded-xl bg-red-500 text-white hover:bg-red-600 transition-colors"
                    aria-label="キャンセル"
                  >
                    <Square className="w-5 h-5" />
                  </button>
                ) : (
                  <button
                    onClick={handleSend}
                    disabled={!input.trim()}
                    className="flex-shrink-0 p-2.5 rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-30 transition-colors"
                    aria-label="送信"
                  >
                    <Send className="w-5 h-5" />
                  </button>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-gray-400 gap-4">
            <Bot className="w-16 h-16 text-gray-300 dark:text-gray-600" />
            <p className="text-sm">チャットセッションを選択または作成してください</p>
            <button
              onClick={() => createMutation.mutate(projectId)}
              className="flex items-center gap-1.5 px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
            >
              <Plus className="w-4 h-4" />
              新規チャットを開始
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
