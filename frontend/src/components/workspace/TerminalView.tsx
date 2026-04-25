import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import { api } from '../../api/client'

interface TerminalViewProps {
  agentId: string
  agentName?: string
  shell?: string
  onDisconnect?: (reason: string) => void
}

interface PendingChar {
  ch: string
  t0: number
}

interface LatencyStats {
  count: number
  p50: number
  p95: number
  last: number
}

const computeStats = (samples: number[]): LatencyStats => {
  if (samples.length === 0) return { count: 0, p50: 0, p95: 0, last: 0 }
  const sorted = [...samples].sort((a, b) => a - b)
  const p = (q: number) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))]
  return { count: samples.length, p50: p(0.5), p95: p(0.95), last: samples[samples.length - 1] }
}

export default function TerminalView({ agentId, agentName, shell, onDisconnect }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const pendingRef = useRef<PendingChar[]>([])
  const samplesRef = useRef<number[]>([])
  const [status, setStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')
  const [stats, setStats] = useState<LatencyStats>({ count: 0, p50: 0, p95: 0, last: 0 })
  const [usingWebgl, setUsingWebgl] = useState(false)

  useEffect(() => {
    let disposed = false
    let cleanupFns: Array<() => void> = []

    const setup = async () => {
      if (!containerRef.current) return

      // ── 1. Fetch one-shot ticket ─────────────────────────
      let ticket: string
      try {
        const res = await api.post('/workspaces/terminal/ticket', { agent_id: agentId })
        ticket = res.data.ticket
      } catch {
        if (!disposed) {
          setStatus('disconnected')
          onDisconnect?.('ticket failed')
        }
        return
      }
      if (disposed) return

      // ── 2. Initialize xterm.js ────────────────────────────
      const terminal = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, Monaco, 'Courier New', monospace",
        theme: {
          background: '#1a1b26', foreground: '#a9b1d6', cursor: '#c0caf5',
          selectionBackground: '#33467c',
        },
      })
      const fitAddon = new FitAddon()
      terminal.loadAddon(fitAddon)
      terminal.open(containerRef.current)

      // WebGL renderer is required for the < 16 ms p50 budget; fall
      // back to the default canvas if the GPU context is unavailable
      // and surface that to the UI so the user knows latency may be
      // worse than expected.
      try {
        const webgl = new WebglAddon()
        webgl.onContextLoss(() => webgl.dispose())
        terminal.loadAddon(webgl)
        setUsingWebgl(true)
      } catch (e) {
        console.warn('[TerminalView] WebGL renderer unavailable, falling back to canvas', e)
        setUsingWebgl(false)
      }

      fitAddon.fit()
      terminalRef.current = terminal
      fitAddonRef.current = fitAddon

      terminal.writeln(`\x1b[36mConnecting to ${agentName ?? agentId}...\x1b[0m`)

      // ── 3. Open WebSocket ─────────────────────────────────
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const params = new URLSearchParams({
        ticket,
        cols: String(terminal.cols),
        rows: String(terminal.rows),
      })
      if (shell) params.set('shell', shell)
      const wsUrl = `${proto}//${window.location.host}/api/v1/workspaces/terminal/ws?${params}`
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        if (!disposed) setStatus('connecting')
      }

      ws.onmessage = (event) => {
        let msg: Record<string, unknown>
        try {
          msg = JSON.parse(event.data) as Record<string, unknown>
        } catch {
          return
        }
        const type = msg.type as string | undefined
        const payload = (msg.payload ?? {}) as Record<string, unknown>

        if (type === 'session_started') {
          setStatus('connected')
          terminal.writeln(`\x1b[32mConnected.\x1b[0m\r\n`)
        } else if (type === 'terminal_output') {
          const data = (payload.data ?? msg.data ?? '') as string
          terminal.write(data)
          // FIFO-match incoming bytes against pending-key timestamps.
          // Latency is measured per character so bash echo (one byte
          // per keypress) gives one sample per keystroke.
          requestAnimationFrame(() => {
            const t1 = performance.now()
            for (const ch of data) {
              const head = pendingRef.current[0]
              if (head && head.ch === ch) {
                samplesRef.current.push(t1 - head.t0)
                pendingRef.current.shift()
              } else if (head) {
                // Mismatch (e.g. control sequence echoed back) — drop
                // the prediction queue rather than try to align it,
                // matching bash line-edit reality where one mismatch
                // means the rest are not echoed verbatim either.
                pendingRef.current.length = 0
                break
              }
            }
            // Throttle stats updates to one render tick to avoid
            // re-rendering on every byte.
            if (samplesRef.current.length > 0) {
              setStats(computeStats(samplesRef.current.slice(-200)))
            }
          })
        } else if (type === 'terminal_exit') {
          terminal.writeln(`\r\n\x1b[33mSession ended (exit ${payload.exit_code ?? '?'})\x1b[0m`)
          setStatus('disconnected')
          onDisconnect?.('session_ended')
        } else if (type === 'error') {
          terminal.writeln(`\r\n\x1b[31mError: ${msg.message ?? 'unknown'}\x1b[0m`)
        }
      }

      ws.onclose = (event) => {
        if (disposed) return
        setStatus('disconnected')
        onDisconnect?.(event.reason || `closed:${event.code}`)
      }

      ws.onerror = () => {
        terminal.writeln(`\r\n\x1b[31mWebSocket error\x1b[0m`)
      }

      const sendInput = (data: string) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'input', data }))
        }
      }

      terminal.onData((data) => {
        const t0 = performance.now()
        for (const ch of data) {
          // Only printable ASCII produces a deterministic echo on
          // bash's default line discipline, so they are the only
          // bytes worth measuring. Control characters (Tab, Enter,
          // arrows, Ctrl-*) round-trip through readline and are not
          // echoed verbatim.
          const code = ch.charCodeAt(0)
          if (code >= 0x20 && code <= 0x7e) {
            pendingRef.current.push({ ch, t0 })
          }
        }
        sendInput(data)
      })

      const handleResize = () => {
        if (disposed) return
        try {
          fitAddon.fit()
        } catch {
          return
        }
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: 'resize',
            cols: terminal.cols,
            rows: terminal.rows,
          }))
        }
      }

      const ro = new ResizeObserver(handleResize)
      ro.observe(containerRef.current)
      cleanupFns.push(() => ro.disconnect())

      // Heartbeat: 30 s ping to keep CF Tunnel from idling out the WS.
      const pingTimer = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 30_000)
      cleanupFns.push(() => window.clearInterval(pingTimer))

      cleanupFns.push(() => {
        try { ws.close() } catch { /* already closed */ }
        terminal.dispose()
        wsRef.current = null
        terminalRef.current = null
        fitAddonRef.current = null
      })
    }

    setup()

    return () => {
      disposed = true
      cleanupFns.forEach((fn) => {
        try { fn() } catch { /* ignore cleanup errors */ }
      })
    }
  }, [agentId, agentName, shell, onDisconnect])

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-3 py-1.5 bg-gray-900 border-b border-gray-700 text-xs text-gray-300">
        <span className={`w-2 h-2 rounded-full ${
          status === 'connected' ? 'bg-green-400' :
          status === 'connecting' ? 'bg-yellow-400 animate-pulse' :
          'bg-red-400'
        }`} />
        <span className="text-gray-400">{agentName ?? agentId}</span>
        <span className="text-gray-600">|</span>
        <span className="text-gray-500">
          {status === 'connected' ? 'Connected' :
           status === 'connecting' ? 'Connecting...' :
           'Disconnected'}
        </span>
        <span className="text-gray-600">|</span>
        <span className={usingWebgl ? 'text-emerald-400' : 'text-amber-400'}>
          {usingWebgl ? 'WebGL' : 'Canvas'}
        </span>
        <span className="text-gray-600">|</span>
        <span className="font-mono">
          n={stats.count} p50={stats.p50.toFixed(1)}ms p95={stats.p95.toFixed(1)}ms
        </span>
      </div>
      <div ref={containerRef} className="flex-1 bg-[#1a1b26] overflow-hidden" />
    </div>
  )
}
