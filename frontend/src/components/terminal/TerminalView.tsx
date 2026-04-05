import { useEffect, useRef, useCallback, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { api } from '../../api/client'

interface TerminalViewProps {
  agentId: string
  agentName: string
  shell?: string
  onDisconnect?: (reason: string) => void
}

export default function TerminalView({ agentId, agentName, shell, onDisconnect }: TerminalViewProps) {
  const termRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const [status, setStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')

  const connect = useCallback(async () => {
    if (!termRef.current) return

    // Get ticket
    let ticket: string
    try {
      const res = await api.post('/terminal/ticket')
      ticket = res.data.ticket
    } catch {
      setStatus('disconnected')
      onDisconnect?.('Failed to get ticket')
      return
    }

    // Initialize terminal
    const terminal = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, Monaco, 'Courier New', monospace",
      theme: {
        background: '#1a1b26',
        foreground: '#a9b1d6',
        cursor: '#c0caf5',
        selectionBackground: '#33467c',
        black: '#32344a',
        red: '#f7768e',
        green: '#9ece6a',
        yellow: '#e0af68',
        blue: '#7aa2f7',
        magenta: '#ad8ee6',
        cyan: '#449dab',
        white: '#787c99',
        brightBlack: '#444b6a',
        brightRed: '#ff7a93',
        brightGreen: '#b9f27c',
        brightYellow: '#ff9e64',
        brightBlue: '#7da6ff',
        brightMagenta: '#bb9af7',
        brightCyan: '#0db9d7',
        brightWhite: '#acb0d0',
      },
    })
    const fitAddon = new FitAddon()
    terminal.loadAddon(fitAddon)
    terminal.open(termRef.current)
    fitAddon.fit()

    terminalRef.current = terminal
    fitAddonRef.current = fitAddon

    terminal.writeln(`\x1b[36mConnecting to ${agentName}...\x1b[0m`)

    // WebSocket connection
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const params = new URLSearchParams({ ticket, agent_id: agentId })
    if (shell) params.set('shell', shell)
    const wsUrl = `${proto}//${window.location.host}/api/v1/terminal/session/ws?${params}`

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setStatus('connected')
      terminal.writeln(`\x1b[32mConnected.\x1b[0m\r\n`)
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'output') {
          terminal.write(msg.data)
        } else if (msg.type === 'session_started') {
          // Session started — terminal ready
        } else if (msg.type === 'session_ended') {
          terminal.writeln(`\r\n\x1b[33mSession ended: ${msg.reason || 'unknown'}\x1b[0m`)
          setStatus('disconnected')
          onDisconnect?.(msg.reason || 'session_ended')
        } else if (msg.type === 'error') {
          terminal.writeln(`\r\n\x1b[31mError: ${msg.message}\x1b[0m`)
        }
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = (event) => {
      if (status !== 'disconnected') {
        terminal.writeln(`\r\n\x1b[33mConnection closed (${event.code})\x1b[0m`)
        setStatus('disconnected')
        onDisconnect?.(event.reason || `closed:${event.code}`)
      }
    }

    ws.onerror = () => {
      terminal.writeln(`\r\n\x1b[31mWebSocket error\x1b[0m`)
    }

    // Forward terminal input to WebSocket
    terminal.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }))
      }
    })

    // Handle resize
    const handleResize = () => {
      fitAddon.fit()
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: 'resize',
          cols: terminal.cols,
          rows: terminal.rows,
        }))
      }
    }

    const resizeObserver = new ResizeObserver(() => handleResize())
    if (termRef.current) {
      resizeObserver.observe(termRef.current)
    }

    // Cleanup
    return () => {
      resizeObserver.disconnect()
      ws.close()
      terminal.dispose()
      wsRef.current = null
      terminalRef.current = null
      fitAddonRef.current = null
    }
  }, [agentId, agentName, shell, onDisconnect])

  useEffect(() => {
    let cleanup: (() => void) | undefined

    connect().then((fn) => {
      cleanup = fn
    })

    return () => {
      cleanup?.()
    }
  }, [connect])

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-900 border-b border-gray-700 text-xs">
        <span className={`w-2 h-2 rounded-full ${status === 'connected' ? 'bg-green-400' : status === 'connecting' ? 'bg-yellow-400 animate-pulse' : 'bg-red-400'}`} />
        <span className="text-gray-400">{agentName}</span>
        <span className="text-gray-600">|</span>
        <span className="text-gray-500">{status === 'connected' ? 'Connected' : status === 'connecting' ? 'Connecting...' : 'Disconnected'}</span>
      </div>
      <div ref={termRef} className="flex-1 bg-[#1a1b26]" />
    </div>
  )
}
