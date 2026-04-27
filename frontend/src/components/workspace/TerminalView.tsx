import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebglAddon } from '@xterm/addon-webgl'
import '@xterm/xterm/css/xterm.css'
import { Zap, ZapOff } from 'lucide-react'
import { api } from '../../api/client'
import { PredictiveEngine } from './PredictiveEngine'

const KILL_SWITCH_STORAGE_KEY = 'webterm:predictiveOff'

const DEBUG_GLOBAL_KEY = '__webterm_debug__'
const DEBUG_LS_KEY = 'webterm:debug'

function debugEnabled(): boolean {
  try {
    if (typeof window === 'undefined') return false
    const w = window as unknown as Record<string, unknown>
    if (w[DEBUG_GLOBAL_KEY]) return true
    if (window.localStorage?.getItem(DEBUG_LS_KEY) === '1') return true
  } catch {
    return false
  }
  return false
}

/** Gated structured log. Enable via window.__webterm_debug__=true or
 *  localStorage.setItem('webterm:debug','1'). Same flag as PredictiveEngine. */
function tlog(event: string, detail?: Record<string, unknown>): void {
  if (!debugEnabled()) return
  if (detail === undefined) console.log(`[terminal] ${event}`)
  else console.log(`[terminal] ${event}`, detail)
}

/** Always-on warning for failure states (stuck replay gate, send on closed
 *  WS, etc). These are rare AND actionable, so they bypass the debug gate. */
function twarn(event: string, detail?: Record<string, unknown>): void {
  if (detail === undefined) console.warn(`[terminal] ${event}`)
  else console.warn(`[terminal] ${event}`, detail)
}

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
  // Refs for props that the setup useEffect must read but must NOT
  // re-run on. ``agentName`` is cosmetic (used once for the
  // "Connecting to <name>..." greeting); ``onSessionStarted`` /
  // ``onDisconnect`` are callbacks whose identity changes whenever
  // their parent's deps update (e.g. when a sibling query
  // resolves), and we must not tear down the WebSocket / xterm
  // Viewport just because a callback was re-allocated.
  const agentNameRef = useRef(agentName)
  const onSessionStartedRef = useRef(onSessionStarted)
  const onDisconnectRef = useRef(onDisconnect)
  // sessionId is also captured in a ref so the setup useEffect
  // does NOT re-run when the parent updates paneConfig.sessionId
  // (e.g. after ``session_started`` assigns the id we just got).
  // The setup uses the *initial* sessionId to decide attach-vs-
  // create at WS open time; subsequent prop changes from the
  // session_started → onConfigChange echo are no-ops for the
  // already-connected session.
  const sessionIdRef = useRef(sessionId)
  // Update during render so the setup callback always reads the
  // freshest values without re-subscribing.
  agentNameRef.current = agentName
  onSessionStartedRef.current = onSessionStarted
  onDisconnectRef.current = onDisconnect
  sessionIdRef.current = sessionId

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
  // Suppress onData while we replay scrollback. xterm.js's ANSI
  // parser auto-replies to DA / DSR / cursor-position queries it
  // sees inside the byte stream by emitting bytes through onData
  // (the same callback used for real keystrokes). Without this gate
  // a reattach that included a prior DA1 query in the scrollback
  // would leak the response back to the PTY as input — the shell
  // then echoes the literal ``^[[?1;2c`` at the prompt.
  const replayingRef = useRef(false)
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
    if (debugEnabled()) {
      tlog('mount', { agentId, sessionIdInitial: sessionIdRef.current })
    } else {
      console.info(
        '[terminal] verbose logging is OFF. Enable with: ' +
        "window.__webterm_debug__ = true  OR  localStorage.setItem('webterm:debug','1') then reload."
      )
    }
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
          onDisconnectRef.current?.('ticket failed')
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

      // Cheap first attempt — usually a no-op when xterm's
      // renderer hasn't reported cell metrics yet.
      try { fitAddon.fit() } catch { /* renderer not ready */ }
      terminalRef.current = terminal
      fitAddonRef.current = fitAddon

      // Schedule a deferred re-fit. xterm's renderer initialises
      // its cell metrics after the first paint; the immediate
      // fit() above bails when ``proposeDimensions`` sees
      // cell.width/height === 0. requestAnimationFrame x2 lands
      // us safely past that.  Also send the resize message so the
      // backend PTY matches what xterm actually computed.
      const refitOnce = () => {
        if (disposed) return
        try { fitAddon.fit() } catch { return }
        const ws = wsRef.current
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: 'resize',
            cols: terminal.cols,
            rows: terminal.rows,
          }))
        }
      }
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(refitOnce)
      })

      terminal.writeln(
        `\x1b[36mConnecting to ${agentNameRef.current ?? agentId}...\x1b[0m`,
      )

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
      // Read initial sessionId from the ref so this setup
      // doesn't depend on the prop (which can change after
      // session_started without us needing to reconnect).
      const initialSessionId = sessionIdRef.current
      if (initialSessionId) params.set('session_id', initialSessionId)
      const wsUrl = `${proto}//${window.location.host}/api/v1/workspaces/terminal/ws?${params}`
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        if (!disposed) setStatus('connecting')
        tlog('ws.onopen', { url: wsUrl })
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
          tlog('session_started', {
            sessionId: msg.session_id,
            attached: Boolean(msg.attached),
            scrollbackChunks: Array.isArray(msg.scrollback) ? msg.scrollback.length : 0,
          })
          const attached = Boolean(msg.attached)
          if (attached) {
            // Replay the agent-side scrollback so the screen looks
            // like it did before the disconnect. Concatenate first so
            // xterm's renderer batches the parse into a single visual
            // flush instead of one repaint per chunk — the latter is
            // what produced the chunky flicker on slow reconnects.
            //
            // Gate ``onData`` for the duration: xterm responds to
            // any DA / cursor-position queries embedded in the
            // scrollback by emitting bytes through the same callback
            // we use for live keystrokes. Without this gate the shell
            // sees the response as typed input and echoes it back at
            // the prompt (e.g. literal ``^[[?1;2c``).
            //
            // Clear the "Connecting…" greeting first so the prompt
            // doesn't appear under a stray banner line.
            replayingRef.current = true
            terminal.write('\x1b[2J\x1b[H')
            const scrollback = (msg.scrollback as string[] | undefined) ?? []
            const combined = scrollback.join('')
            const lift = () => {
              requestAnimationFrame(() => {
                replayingRef.current = false
                tlog('replay.lift', { combinedBytes: combined.length })
              })
            }
            if (combined.length > 0) {
              terminal.write(combined, lift)
            } else {
              lift()
            }
            // No status banner on reattach — extra writeln'd lines
            // shift the prompt and break TUI layouts whose cursor
            // position was captured in the scrollback (e.g. vim,
            // less, fzf). Connection state is already surfaced in the
            // pane header; the visible terminal stays untouched.
          } else {
            terminal.writeln(`\x1b[32mConnected.\x1b[0m\r\n`)
          }
          const sid = msg.session_id as string | undefined
          if (sid && !initialSessionId && onSessionStartedRef.current) {
            onSessionStartedRef.current(sid)
          }
        } else if (type === 'terminal_output') {
          // Capture echo arrival time IMMEDIATELY at WS receive,
          // before any rAF / paint work. performance.now() inside
          // requestAnimationFrame conflates echo arrival with the
          // next-paint deadline, and rAF throttles to ~1Hz on
          // hidden tabs — that alone pushed p95 to ~1.9s in
          // dogfooding. The rAF below is kept as a batching defer
          // for setStats but uses this captured timestamp.
          const t1 = performance.now()
          const data = (payload.data ?? msg.data ?? '') as string
          // Engine inspects bytes BEFORE xterm.js renders them so the
          // FIFO advances in lockstep with the visible frame.
          // v7: engine wraps echo with CHA so bytes land at server's cursor
          // (not at predict tip where xterm cursor sits in v7).
          const wrapped = engineRef.current?.processServerData(data) ?? data
          terminal.write(wrapped)
          // FIFO-match incoming bytes against pending-key timestamps.
          // Latency is measured per character so bash echo (one byte
          // per keypress) gives one sample per keystroke.
          requestAnimationFrame(() => {
            // t1 captured at WS receive above, NOT inside this rAF.
            // Garbage-collect predictions older than STALE_MS before
            // matching: a stale head pollutes p95 with multi-second
            // samples when it eventually matches a much later echo
            // (e.g. the user typed during a connect blip and the
            // first real echo arrives seconds later).
            const STALE_MS = 500
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
        twarn('ws.onclose', { code: event.code, reason: event.reason || '(none)' })
        onDisconnect?.(event.reason || `closed:${event.code}`)
      }

      ws.onerror = () => {
        terminal.writeln(`\r\n\x1b[31mWebSocket error\x1b[0m`)
        twarn('ws.onerror', { wsState: ws.readyState })
      }

      const sendInput = (data: string) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'input', data }))
          tlog('sendInput.ok', { bytes: data.length, wsState: ws.readyState })
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
        // Drop xterm-generated responses to embedded DA / DSR queries
        // during scrollback replay. See ``replayingRef`` declaration
        // for the failure mode this guards against.
        if (replayingRef.current) {
          // onData is gated during scrollback replay because xterm emits
          // bytes through this same callback when it auto-replies to
          // DA/DSR queries embedded in the scrollback. Log so we can
          // tell legitimate gating apart from stuck-state input loss.
          tlog('onData gated by replayingRef', {
            bytes: data.length,
            first: data.length > 0 ? data.charCodeAt(0).toString(16) : null,
          })
          return
        }
        tlog('onData', {
          bytes: data.length,
          first: data.length > 0 ? data.charCodeAt(0).toString(16) : null,
          measuring: measuringRef.current,
          wsState: ws.readyState,
          replayingNow: replayingRef.current,
        })
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

      // Drop in-flight pending samples whenever the tab goes
      // hidden — rAF / timer throttling guarantees their t0 will
      // be ancient by the time we return, and a single late match
      // dominates p95 for the rest of the session. Cheap reset.
      const handleVisibility = () => {
        const state = document.visibilityState
        const cleared = pendingRef.current.length
        if (state !== 'visible') {
          pendingRef.current.length = 0
        }
        tlog('visibility', { state, clearedPending: state !== 'visible' ? cleared : 0 })
      }
      document.addEventListener('visibilitychange', handleVisibility)
      cleanupFns.push(() => document.removeEventListener('visibilitychange', handleVisibility))

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
  }, [agentId, shell])

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
