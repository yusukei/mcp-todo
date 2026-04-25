import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import { Zap, ZapOff } from 'lucide-react'
import { api } from '../../api/client'
import { PredictiveEngine } from './PredictiveEngine'

const KILL_SWITCH_STORAGE_KEY = 'webterm:predictiveOff'

interface TerminalViewProps {
  agentId: string
  agentName?: string
  shell?: string
  /**
   * If present, the WebSocket attaches to an existing PTY session
   * (replaying the agent-side scrollback). Absent = create a fresh
   * session; the backend assigns the id and returns it in
   * ``session_started``, at which point ``onSessionStarted`` fires.
   */
  sessionId?: string
  onSessionStarted?: (sessionId: string) => void
  onDisconnect?: (reason: string) => void
}

/**
 * Imperative handle exposed via ``ref`` so callers (e.g. workbench
 * panes routing cross-pane events) can inject input into the PTY
 * without forcing a prop-driven re-render that would tear down the
 * terminal.
 */
export interface TerminalViewHandle {
  /** Send a raw string to the PTY (no echo, no prediction). Returns
   *  ``true`` if the WS was open and the bytes were enqueued. */
  sendInput: (data: string) => boolean
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

const TerminalView = forwardRef<TerminalViewHandle, TerminalViewProps>(function TerminalView({
  agentId,
  agentName,
  shell,
  sessionId,
  onSessionStarted,
  onDisconnect,
}, ref) {
  const containerRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const engineRef = useRef<PredictiveEngine | null>(null)
  const pendingRef = useRef<PendingChar[]>([])
  const samplesRef = useRef<number[]>([])

  // Expose ``sendInput`` through the ref so a cross-pane event (e.g.
  // FileBrowserPane → ``cd <path>``) can inject keystrokes without
  // going through the prediction engine. Bytes go straight to the
  // WebSocket; the agent's PTY echoes them back through the normal
  // output pipeline.
  useImperativeHandle(
    ref,
    () => ({
      sendInput: (data: string) => {
        const ws = wsRef.current
        if (!ws || ws.readyState !== WebSocket.OPEN) return false
        ws.send(JSON.stringify({ type: 'input', data }))
        return true
      },
    }),
    [],
  )
  // Latency measurement only counts after session_started; before that
  // the echo path is not yet established and any typed bytes would
  // match against the first prompt redraw seconds later, polluting
  // p95.
  const measuringRef = useRef(false)
  const [status, setStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')
  const [stats, setStats] = useState<LatencyStats>({ count: 0, p50: 0, p95: 0, last: 0 })
  const [usingWebgl, setUsingWebgl] = useState(false)
  const [predictiveOff, setPredictiveOff] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(KILL_SWITCH_STORAGE_KEY) === '1'
    } catch {
      return false
    }
  })

  // Sync the engine's kill switch with the React-state-backed UI
  // toggle. The engine is the source of truth for prediction state;
  // this effect just mirrors the user-facing flag back into it.
  useEffect(() => {
    engineRef.current?.setKillSwitch(predictiveOff)
    try {
      window.localStorage.setItem(KILL_SWITCH_STORAGE_KEY, predictiveOff ? '1' : '0')
    } catch {
      /* localStorage unavailable; in-memory toggle still works */
    }
  }, [predictiveOff])

  // Ctrl+Shift+P toggle while focus is anywhere on the page.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault()
        setPredictiveOff((v) => !v)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

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
      // Phase A: session_id in the query asks the backend to ATTACH
      // to an existing session. Without it the backend creates a
      // fresh one and echoes the id back in ``session_started``.
      if (sessionId) params.set('session_id', sessionId)
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
          // Drop anything queued during the handshake so we only
          // measure round-trips that happen on a live PTY.
          pendingRef.current.length = 0
          samplesRef.current.length = 0
          measuringRef.current = true
          const attached = Boolean(msg.attached)
          if (attached) {
            // Replay the agent-side scrollback so the screen looks
            // like it did before the disconnect. Each entry is the
            // raw byte-stream chunk the PTY emitted; xterm's ANSI
            // parser handles the colour codes and cursor moves
            // exactly as it would have during live input.
            const scrollback = (msg.scrollback as string[] | undefined) ?? []
            for (const chunk of scrollback) {
              terminal.write(chunk)
            }
            const exited = Boolean(msg.exited)
            if (exited) {
              terminal.writeln(
                `\r\n\x1b[33m[reattached to an exited session — read-only]\x1b[0m`,
              )
            } else {
              terminal.writeln(
                `\r\n\x1b[36m[reattached]\x1b[0m`,
              )
            }
          } else {
            terminal.writeln(`\x1b[32mConnected.\x1b[0m\r\n`)
          }
          const sid = msg.session_id as string | undefined
          if (sid && !sessionId && onSessionStarted) {
            onSessionStarted(sid)
          }
        } else if (type === 'terminal_output') {
          const data = (payload.data ?? msg.data ?? '') as string
          // Engine inspects bytes BEFORE xterm.js renders them so the
          // FIFO advances in lockstep with the visible frame.
          engineRef.current?.onServerData(data)
          terminal.write(data)
          // FIFO-match incoming bytes against pending-key timestamps.
          // Latency is measured per character so bash echo (one byte
          // per keypress) gives one sample per keystroke.
          requestAnimationFrame(() => {
            const t1 = performance.now()
            // Garbage-collect predictions older than 2s before
            // matching: a stale head pollutes p95 with multi-second
            // samples when it eventually matches a much later echo
            // (e.g. the user typed during a connect blip and the
            // first real echo arrives seconds later).
            const STALE_MS = 2000
            while (
              pendingRef.current.length > 0 &&
              t1 - pendingRef.current[0].t0 > STALE_MS
            ) {
              pendingRef.current.shift()
            }
            // FIFO match without rollback: bash interleaves ESC
            // sequences (cursor save/restore, bracketed paste, color
            // resets) around echoed bytes. A strict head-only match
            // resets the queue at every ESC and yields zero samples.
            // Skip non-matching bytes and keep the head waiting until
            // the predicted char actually shows up.
            for (const ch of data) {
              const head = pendingRef.current[0]
              if (head && head.ch === ch) {
                const dt = t1 - head.t0
                pendingRef.current.shift()
                if (dt < STALE_MS) {
                  samplesRef.current.push(dt)
                }
              }
            }
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

      const engine = new PredictiveEngine({
        terminal,
        onSend: sendInput,
      })
      engine.setKillSwitch(predictiveOff)
      engineRef.current = engine
      cleanupFns.push(() => {
        engine.dispose()
        engineRef.current = null
      })

      terminal.onData((data) => {
        const t0 = performance.now()
        if (measuringRef.current) {
          for (const ch of data) {
            // Only printable ASCII produces a deterministic echo on
            // bash's default line discipline. Control bytes (Tab,
            // Enter, arrows, Ctrl-*) round-trip through readline and
            // are not echoed verbatim, so they are not measured.
            const code = ch.charCodeAt(0)
            if (code >= 0x20 && code <= 0x7e) {
              pendingRef.current.push({ ch, t0 })
            }
          }
        }
        // Engine sends + paints predictions in one call.
        engine.onUserInput(data)
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
  }, [agentId, agentName, shell, sessionId, onSessionStarted, onDisconnect])

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
        <button
          type="button"
          onClick={() => setPredictiveOff((v) => !v)}
          className={`ml-auto flex items-center gap-1 px-2 py-0.5 rounded text-xs ${
            predictiveOff
              ? 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              : 'bg-emerald-900/40 text-emerald-300 hover:bg-emerald-900/60'
          }`}
          title={`Predictive ${predictiveOff ? 'OFF' : 'ON'} (Ctrl+Shift+P)`}
        >
          {predictiveOff
            ? <><ZapOff className="w-3 h-3" /> Predict: OFF</>
            : <><Zap className="w-3 h-3" /> Predict: ON</>}
        </button>
      </div>
      <div ref={containerRef} className="flex-1 bg-[#1a1b26] overflow-hidden" />
    </div>
  )
})

export default TerminalView
