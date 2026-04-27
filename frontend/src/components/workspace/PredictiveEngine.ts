import type { Terminal, IDisposable } from '@xterm/xterm'

/**
 * PredictiveEngine — local-echo speculation for the Web Terminal.
 *
 * Strategy (v6 — Anchored Overlay with Cosmetic Misprediction):
 *
 *   - We maintain an explicit ``anchor: {x, y}`` separate from xterm's cursor.
 *     Anchor is set when ``pending`` transitions empty → non-empty and stays
 *     fixed for the duration of the burst.
 *   - Each predicted char is written at an *absolute* column via CSI CHA
 *     (``\x1b[NG``), not via relative CUF (``\x1b[NC``). CHA is immune to
 *     the cursor lag introduced by xterm's async parser, which is the
 *     mechanism that broke v4's relative-advance model.
 *   - Predicted glyphs are painted with dim+underline SGR
 *     (``\x1b[2;4m...\x1b[m``) so a misprediction looks "tentative" instead
 *     of "broken". Server echo for the same byte overwrites the tentative
 *     glyph with normal SGR.
 *   - Rollback is anchor-based: write spaces from anchor.x for
 *     ``pending.length`` cells. Independent of xterm's current cursor,
 *     guaranteed to clear what we painted.
 *
 * Rollback triggers (Web Terminal v2 spec §4.3):
 *   - Server emits CSI K / J (clear EOL/screen) — readline line rewrite
 *   - Server emits CSI G (CHA) / H,f (CUP) / d (VPA) — explicit cursor jump
 *   - Server emits CR or LF while pending non-empty — line redraw boundary
 *   - Server's printable byte does NOT match ``pending[0]`` — misprediction
 *   - Anchor row drift detected on next predict (best-effort)
 *   - User types non-printable / non-BS byte (Enter, Tab, Ctrl-*) — fence
 *   - 1500ms gcStale timeout (high-RTT tolerant; see PREDICT_TIMEOUT_MS)
 *   - Alt-screen / bracketed-paste / kill-switch (existing handlers)
 *
 * NOT a rollback trigger:
 *   - ``\x1b[?2004h/l`` toggles — too frequent. Treated as ESC quiet only.
 *
 * Diagnostic logging:
 *   ``window.__webterm_debug__ = true`` (or
 *   ``localStorage.setItem('webterm:debug', '1')``) emits ``console.log``
 *   traces of every state transition. Off by default.
 */

// Bumped 300 → 1500ms: real RTT over CF Tunnel + MAP-E is ~350-700ms,
// and the previous 300ms TO fired BEFORE every echo arrived, causing a
// rollback storm where every keystroke flickered into a space and the
// next echo landed at a freshly-reanchored (wrong) column. Visually
// this looked like the cursor was rewinding. 1500ms covers RTT up to
// ~1.5s safely; misprediction on no-echo modes (vi etc) stays visible
// longer but is automatically caught by mode detection (alt-screen).
const PREDICT_TIMEOUT_MS = 1500
const PENDING_LIMIT = 32
const ESC_QUIET_MS = 200

interface PredictedChar {
  ch: string
  col: number     // 0-indexed column where this char was painted
  insertedAt: number
}

interface Anchor {
  x: number
  y: number
}

export interface PredictionMetrics {
  predicted: number
  confirmed: number
  rolledBack: number
}

export interface PredictiveEngineOptions {
  terminal: Terminal
  onSend: (data: string) => void
  onMetrics?: (m: PredictionMetrics) => void
}

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

function escapeForLog(s: string): string {
  return s.replace(/[\x00-\x1f\x7f]/g, (c) => {
    if (c === '\x1b') return '\\e'
    if (c === '\b') return '\\b'
    if (c === '\x7f') return '\\x7f'
    if (c === '\r') return '\\r'
    if (c === '\n') return '\\n'
    if (c === '\t') return '\\t'
    return `\\x${c.charCodeAt(0).toString(16).padStart(2, '0')}`
  })
}

interface EscapeInspectResult {
  newI: number
  /** True if this CSI is a "dangerous" cursor/clear op that invalidates predictions. */
  dangerous: boolean
  /** Final byte for diagnostic logging (only meaningful for CSI). */
  final: string
}

export class PredictiveEngine {
  private terminal: Terminal
  private onSend: (data: string) => void
  private onMetrics?: (m: PredictionMetrics) => void
  private killSwitchOff = false
  private pending: PredictedChar[] = []
  private anchor: Anchor | null = null
  /** 0-indexed column where the NEXT predicted char will be painted. */
  private nextCol = 0
  /** 0-indexed column where the SERVER's cursor currently sits (= anchor.x +
   *  confirmed_count). Only valid while ``anchor !== null``. v7: used to
   *  reposition xterm cursor when an echo arrives so the byte lands at the
   *  server's actual column rather than at the predict tip. */
  private serverCol = 0
  private metrics: PredictionMetrics = {
    predicted: 0,
    confirmed: 0,
    rolledBack: 0,
  }
  private lastEscFromServer = 0
  private altScreen = false
  private cursorKeysApp = false
  private mouseTracking = false
  private bracketedPaste = false
  private timeoutTimer: ReturnType<typeof setTimeout> | null = null
  private disposers: IDisposable[] = []

  constructor(opts: PredictiveEngineOptions) {
    this.terminal = opts.terminal
    this.onSend = opts.onSend
    this.onMetrics = opts.onMetrics
    this.installCsiHandlers()
    this.log('engine constructed (v6 anchored overlay)')
  }

  /** Forward keyboard input to the server, optionally painting predictions. */
  onUserInput(data: string): void {
    // BS/DEL — pop the rightmost predicted cell when pending is non-empty,
    // otherwise pass through.
    if (data === '\x7f' || data === '\b') {
      this.onSend(data)
      const reason = !this.canPredict()
        ? this.suppressionReason()
        : this.pending.length === 0
        ? 'pending-empty'
        : null
      if (reason) {
        this.log(`onUserInput BS suppressed: ${reason}`, { data: escapeForLog(data) })
        return
      }
      const last = this.pending[this.pending.length - 1]
      if (!last || this.anchor === null) return
      const cha = last.col + 1 // 1-indexed
      // v7: cursor follows tip. After erasing, position cursor at:
      //  - new predict tip (last.col) if predicts remain
      //  - serverCol if this BS empties pending
      const willEmpty = this.pending.length === 1
      const cursorEndCol = willEmpty ? this.serverCol : last.col
      const seq = `\x1b[${cha}G \x1b[${cursorEndCol + 1}G`
      this.terminal.write(seq)
      this.pending.pop()
      this.nextCol = last.col
      if (this.pending.length === 0) {
        this.anchor = null
        this.nextCol = 0
        this.serverCol = 0
      }
      this.metrics.rolledBack += 1
      this.log('onUserInput BS painted erase', {
        col: last.col,
        popped: last.ch,
        pending: this.pending.length,
        write: escapeForLog(seq),
      })
      this.emitMetrics()
      return
    }

    // Non-printable single bytes (Enter, Tab, Ctrl-*) — these typically
    // trigger a server-side line redraw or scope change. Treat as a fence:
    // roll back anything pending, then pass through. The user's intent
    // is unambiguous and our predicted cells, if any, will not match the
    // server's response in any predictable way.
    if (data.length === 1) {
      const code = data.charCodeAt(0)
      if (code < 0x20 || code === 0x7f) {
        if (this.pending.length > 0) {
          this.log(`onUserInput non-printable → rollback`, { byte: code })
          this.rollbackVisual(0)
        }
        this.onSend(data)
        return
      }
    }

    this.onSend(data)
    const reason = this.canPredict() ? null : this.suppressionReason()
    if (reason) {
      this.log(`onUserInput suppressed: ${reason}`, {
        data: escapeForLog(data),
        pending: this.pending.length,
      })
      return
    }

    // Anchor row drift: if the cursor has moved row since the burst started,
    // our predicted column anchor is meaningless. Roll back, then start a
    // fresh burst on the current row.
    const cursorX = this.terminal.buffer.active.cursorX
    const cursorY = this.terminal.buffer.active.cursorY
    if (this.anchor !== null && this.anchor.y !== cursorY) {
      this.log('onUserInput anchor row drift → rollback', {
        anchor: this.anchor,
        cursorY,
      })
      this.rollbackVisual(0)
    }

    if (this.anchor === null) {
      this.anchor = { x: cursorX, y: cursorY }
      this.nextCol = cursorX
      this.serverCol = cursorX
    }

    const cols = this.terminal.cols
    if (this.nextCol >= cols - 1) {
      // At/past line end — autowrap territory; bail out.
      this.log('onUserInput skip predict: at row edge', {
        cols,
        nextCol: this.nextCol,
      })
      // If anchor was just created on this call but we paint nothing, drop it.
      if (this.pending.length === 0) {
        this.anchor = null
        this.nextCol = 0
        this.serverCol = 0
      }
      return
    }

    const candidates: PredictedChar[] = []
    const now = performance.now()
    let probeCol = this.nextCol
    for (const ch of data) {
      const code = ch.charCodeAt(0)
      if (code < 0x20 || code > 0x7e) break
      if (this.pending.length + candidates.length >= PENDING_LIMIT) break
      if (probeCol >= cols - 1) break
      candidates.push({ ch, col: probeCol, insertedAt: now })
      probeCol += 1
    }
    if (candidates.length === 0) {
      this.log('onUserInput skip predict: no printable candidates', {
        data: escapeForLog(data),
      })
      if (this.pending.length === 0) {
        this.anchor = null
        this.nextCol = 0
        this.serverCol = 0
      }
      return
    }

    const text = candidates.map((c) => c.ch).join('')
    const cha = candidates[0].col + 1 // 1-indexed
    // Save → CHA absolute → dim+underline SGR → text → reset SGR → restore.
    // CHA is immune to the cursor-lag race that broke v4.
    // v7: removed \e[s/\e[u — let cursor advance naturally to the predict
    // tip (= probeCol = nextCol after this paint). Cursor follows the
    // user's typing position; echo path repositions to serverCol via
    // processServerData wrapper.
    const seq = `\x1b[${cha}G\x1b[2;4m${text}\x1b[m`
    this.terminal.write(seq)
    this.pending.push(...candidates)
    this.nextCol = probeCol
    this.metrics.predicted += candidates.length
    this.scheduleTimeout()
    this.log('onUserInput painted predict', {
      anchor: this.anchor,
      cha,
      text: escapeForLog(text),
      pending: this.pending.length,
      nextCol: this.nextCol,
      write: escapeForLog(seq),
    })
    this.emitMetrics()
  }

  /**
   * Inspect a server output frame for matches against predicted chars,
   * and trigger rollback when the frame indicates a line redraw or cursor
   * jump.
   *
   * Must be called BEFORE ``terminal.write(data)`` so the rollback bytes
   * we write here land in xterm's parser queue ahead of the server bytes.
   */
  onServerData(data: string): void {
    this.gcStale()

    const startedPending = this.pending.length
    let matched = 0
    let rolledHere = 0
    const events: string[] = []
    let i = 0
    while (i < data.length) {
      const code = data.charCodeAt(i)
      if (code === 0x1b) {
        const result = this.skipAndInspectEscape(data, i)
        this.lastEscFromServer = performance.now()
        if (result.dangerous && this.pending.length > 0) {
          events.push(`CSI ${result.final}`)
          rolledHere += this.pending.length
          this.rollbackVisual(0)
        }
        i = result.newI
        continue
      }
      if ((code === 0x0d || code === 0x0a) && this.pending.length > 0) {
        // CR or LF mid-stream while we have pending — line boundary.
        events.push(code === 0x0d ? 'CR' : 'LF')
        rolledHere += this.pending.length
        this.rollbackVisual(0)
        i += 1
        continue
      }
      if (code >= 0x20 && code <= 0x7e) {
        const head = this.pending[0]
        if (head) {
          if (head.ch === data[i]) {
            this.pending.shift()
            this.metrics.confirmed += 1
            this.serverCol += 1
            matched += 1
            if (this.pending.length === 0) {
              this.anchor = null
              this.nextCol = 0
        this.serverCol = 0
            }
          } else {
            // Server printed a byte that contradicts our prediction. The
            // prediction was wrong — roll back. The non-matching byte itself
            // will be rendered by xterm at its current cursor when terminal
            // .write(data) runs after this method returns.
            events.push(`mismatch ${data[i]}≠${head.ch}`)
            rolledHere += this.pending.length
            this.rollbackVisual(0)
          }
        }
      }
      // Other control bytes (Tab, BS from server side, etc.) — leave alone.
      i += 1
    }

    if (this.pending.length === 0 && this.timeoutTimer != null) {
      clearTimeout(this.timeoutTimer)
      this.timeoutTimer = null
    }
    if (this.metrics.predicted > 0) this.emitMetrics()

    if (startedPending > 0 || rolledHere > 0 || events.length > 0) {
      this.log('onServerData', {
        bytes: data.length,
        preview: escapeForLog(data.slice(0, 64)),
        startedPending,
        matched,
        rolledHere,
        pendingNow: this.pending.length,
        events,
      })
    }
  }

  /**
   * v7: Wrap a server data frame with cursor-positioning escape sequences
   * and run the existing onServerData state machine. The returned string
   * is what the caller should pass to ``terminal.write``; it ensures echo
   * bytes land at the server's cursor (rather than the predict tip where
   * v7 leaves the visible cursor) and that the cursor is restored to the
   * predict tip after the echo so the user keeps seeing the cursor where
   * they're typing.
   *
   * Old pattern (v6): engine.onServerData(data); terminal.write(data)
   * New pattern (v7): terminal.write(engine.processServerData(data))
   */
  processServerData(data: string): string {
    const beforeServerCol = this.anchor !== null ? this.serverCol : null
    this.onServerData(data)
    const afterNextCol = this.anchor !== null ? this.nextCol : null
    let out = data
    if (beforeServerCol !== null) {
      out = `\x1b[${beforeServerCol + 1}G` + out
    }
    if (afterNextCol !== null) {
      out = out + `\x1b[${afterNextCol + 1}G`
    }
    return out
  }

  toggleKillSwitch(): boolean {
    this.killSwitchOff = !this.killSwitchOff
    if (this.killSwitchOff) this.rollbackVisual(0)
    this.log(`killSwitch toggled → ${this.killSwitchOff ? 'OFF (no predict)' : 'ON (predict)'}`)
    return this.killSwitchOff
  }

  setKillSwitch(off: boolean): void {
    if (this.killSwitchOff === off) return
    this.killSwitchOff = off
    if (off) this.rollbackVisual(0)
    this.log(`killSwitch set → ${off ? 'OFF (no predict)' : 'ON (predict)'}`)
  }

  isKillSwitchOff(): boolean {
    return this.killSwitchOff
  }

  isActive(): boolean {
    return !this.killSwitchOff && !this.altScreen
  }

  onDisconnect(): void {
    if (this.pending.length > 0) this.rollbackVisual(0)
    this.log('onDisconnect → rollback all')
  }

  getMetrics(): PredictionMetrics {
    return { ...this.metrics }
  }

  dispose(): void {
    if (this.timeoutTimer != null) {
      clearTimeout(this.timeoutTimer)
      this.timeoutTimer = null
    }
    for (const d of this.disposers) {
      try { d.dispose() } catch { /* ignore disposer errors */ }
    }
    this.disposers = []
  }

  // ── Internals ──────────────────────────────────────────────

  private canPredict(): boolean {
    return this.suppressionReason() == null
  }

  private suppressionReason(): string | null {
    if (this.killSwitchOff) return 'kill-switch-off'
    if (this.altScreen) return 'alt-screen'
    if (this.cursorKeysApp) return 'cursor-keys-app'
    if (this.mouseTracking) return 'mouse-tracking'
    if (this.bracketedPaste) return 'bracketed-paste'
    if (performance.now() - this.lastEscFromServer < ESC_QUIET_MS) return 'esc-quiet-window'
    return null
  }

  /**
   * Visually clear the predicted cells from index ``fromIdx`` onward and
   * truncate the FIFO accordingly. Anchor-relative; does not depend on
   * xterm's current cursor position.
   */
  private rollbackVisual(fromIdx: number): void {
    if (this.anchor === null) return
    const count = this.pending.length - fromIdx
    if (count <= 0) {
      if (this.pending.length === 0) {
        this.anchor = null
        this.nextCol = 0
        this.serverCol = 0
      }
      return
    }
    const startCol = this.pending[fromIdx]?.col ?? this.anchor.x
    const cha = startCol + 1
    const spaces = ' '.repeat(count)
    // v7: cursor follows predict tip. After erasing, position cursor:
    //  - full rollback (fromIdx=0): at serverCol so the next typed char's
    //    anchor is established at server's actual position
    //  - partial: at new predict tip
    const cursorEndCol = fromIdx === 0
      ? this.serverCol
      : (this.pending[fromIdx - 1]?.col ?? this.anchor.x) + 1
    const seq = `\x1b[${cha}G${spaces}\x1b[${cursorEndCol + 1}G`
    this.terminal.write(seq)
    this.metrics.rolledBack += count
    this.pending.length = fromIdx
    if (this.pending.length === 0) {
      this.anchor = null
      this.nextCol = 0
      this.serverCol = 0
    } else {
      const last = this.pending[this.pending.length - 1]
      this.nextCol = last.col + 1
    }
    this.log('rollbackVisual', {
      fromIdx,
      count,
      startCol,
      pendingNow: this.pending.length,
      write: escapeForLog(seq),
    })
    this.emitMetrics()
  }

  private gcStale(): void {
    if (this.pending.length === 0) return
    const now = performance.now()
    const head = this.pending[0]
    if (head && now - head.insertedAt > PREDICT_TIMEOUT_MS) {
      this.log(`gcStale rollback (head ${(now - head.insertedAt).toFixed(0)}ms old)`, {
        pending: this.pending.length,
      })
      this.rollbackVisual(0)
    }
  }

  private scheduleTimeout(): void {
    if (this.timeoutTimer != null) return
    this.timeoutTimer = setTimeout(() => {
      this.timeoutTimer = null
      this.gcStale()
    }, PREDICT_TIMEOUT_MS + 50)
  }

  /**
   * Walk past one ESC sequence starting at index ``i`` and report whether
   * its CSI final byte indicates a cursor/clear op that invalidates our
   * predictions.
   *
   * Dangerous CSI finals (Web Terminal v2 spec §4.3):
   *   K — Erase in Line
   *   J — Erase in Display
   *   G — Cursor Horizontal Absolute (CHA)
   *   H — Cursor Position (CUP)
   *   f — same as H (HVP)
   *   d — Vertical Position Absolute (VPA)
   *   A/B/C/D/E/F — cursor up/down/forward/back/next-line/prev-line (large enough to leave anchor row)
   *
   * Note: ``?h``/``?l`` (private mode set/reset, e.g. ?2004h) is NOT
   * dangerous — it goes through ``installCsiHandlers``. We only check
   * unprefixed CSI here.
   */
  private skipAndInspectEscape(data: string, i: number): EscapeInspectResult {
    if (i + 1 >= data.length) return { newI: i + 1, dangerous: false, final: '' }
    const next = data[i + 1]
    if (next === '[') {
      // CSI: ESC [ params final, where final is 0x40-0x7e
      let j = i + 2
      let hasPrivatePrefix = false
      // Skip over any private prefix (?, !, > etc.) so we only flag the
      // bare CSI sequences that move/clear.
      while (j < data.length) {
        const c = data.charCodeAt(j)
        if (c >= 0x40 && c <= 0x7e) {
          const final = data[j]
          // Private-prefixed sequences (?h, ?l etc.) handled elsewhere — never dangerous here.
          const dangerous = !hasPrivatePrefix && 'KJGHfdABCDEF'.includes(final)
          return { newI: j + 1, dangerous, final }
        }
        // Note private-prefix bytes (0x3c-0x3f) but stay in the parameter loop.
        if (c >= 0x3c && c <= 0x3f && j === i + 2) hasPrivatePrefix = true
        j += 1
      }
      return { newI: j, dangerous: false, final: '' }
    }
    if (next === ']') {
      // OSC: ESC ] params (BEL | ESC \\)
      let j = i + 2
      while (j < data.length) {
        if (data.charCodeAt(j) === 0x07) return { newI: j + 1, dangerous: false, final: '' }
        if (data[j] === '\\' && j > 0 && data.charCodeAt(j - 1) === 0x1b) {
          return { newI: j + 1, dangerous: false, final: '' }
        }
        j += 1
      }
      return { newI: j, dangerous: false, final: '' }
    }
    return { newI: i + 2, dangerous: false, final: '' }
  }

  private emitMetrics(): void {
    if (this.onMetrics) this.onMetrics({ ...this.metrics })
  }

  private log(msg: string, ctx?: Record<string, unknown>): void {
    if (!debugEnabled()) return
    if (ctx) console.log(`[predict] ${msg}`, ctx)
    else console.log(`[predict] ${msg}`)
  }

  private installCsiHandlers(): void {
    this.disposers.push(
      this.terminal.parser.registerCsiHandler({ prefix: '?', final: 'h' }, (params) => {
        for (const p of params) {
          const v = Array.isArray(p) ? p[0] : p
          if (v === 1049 || v === 47 || v === 1047 || v === 1048) {
            this.altScreen = true
            this.rollbackVisual(0)
            this.log('CSI ?h alt-screen ON', { v })
          } else if (v === 1) {
            this.cursorKeysApp = true
            this.log('CSI ?1h cursor-keys-app ON')
          } else if (v === 1000 || v === 1006 || v === 1015) {
            this.mouseTracking = true
            this.log('CSI ?h mouse-tracking ON', { v })
          }
        }
        return false
      }),
    )
    this.disposers.push(
      this.terminal.parser.registerCsiHandler({ prefix: '?', final: 'l' }, (params) => {
        for (const p of params) {
          const v = Array.isArray(p) ? p[0] : p
          if (v === 1049 || v === 47 || v === 1047 || v === 1048) {
            this.altScreen = false
            this.log('CSI ?l alt-screen OFF', { v })
          } else if (v === 1) {
            this.cursorKeysApp = false
            this.log('CSI ?1l cursor-keys-app OFF')
          } else if (v === 1000 || v === 1006 || v === 1015) {
            this.mouseTracking = false
            this.log('CSI ?l mouse-tracking OFF', { v })
          }
        }
        return false
      }),
    )
    this.disposers.push(
      this.terminal.parser.registerCsiHandler({ final: '~' }, (params) => {
        for (const p of params) {
          const v = Array.isArray(p) ? p[0] : p
          if (v === 200) {
            this.bracketedPaste = true
            this.rollbackVisual(0)
            this.log('CSI 200~ bracketed-paste START')
          } else if (v === 201) {
            this.bracketedPaste = false
            this.log('CSI 201~ bracketed-paste END')
          }
        }
        return false
      }),
    )
  }
}
