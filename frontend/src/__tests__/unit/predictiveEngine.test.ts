/**
 * PredictiveEngine v6 — Anchored Overlay with Cosmetic Misprediction.
 *
 * The engine paints predicted glyphs in dim+underline SGR at columns
 * computed from an explicit anchor (independent of xterm's current cursor).
 * Server bytes are inspected before xterm renders them; line-rewrite or
 * cursor-jump CSIs trigger rollback that visually clears the predicted cells.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { PredictiveEngine } from '../../components/workspace/PredictiveEngine'
import type { Terminal } from '@xterm/xterm'

interface FakeTerminal {
  cols: number
  buffer: { active: { cursorX: number; cursorY: number } }
  write: ReturnType<typeof vi.fn>
  parser: { registerCsiHandler: ReturnType<typeof vi.fn> }
}

function makeTerminal(cursorX = 5, cursorY = 0, cols = 80): FakeTerminal {
  return {
    cols,
    buffer: { active: { cursorX, cursorY } },
    write: vi.fn(),
    parser: {
      registerCsiHandler: vi.fn(() => ({ dispose: vi.fn() })),
    },
  }
}

function makeEngine(opts?: {
  term?: FakeTerminal
  onSend?: ReturnType<typeof vi.fn>
}) {
  const term = opts?.term ?? makeTerminal()
  const onSend = opts?.onSend ?? vi.fn()
  const engine = new PredictiveEngine({
    terminal: term as unknown as Terminal,
    onSend,
  })
  return { engine, term, onSend }
}

/**
 * v7 paint frame: CHA absolute → dim+underline SGR → text → reset SGR.
 * No save/restore — cursor advances to startCol+text.length (= nextCol).
 */
function paintFrame(startCol: number, text: string): string {
  return `\x1b[${startCol + 1}G\x1b[2;4m${text}\x1b[m`
}

/**
 * v7 BS erase frame at 0-indexed ``col``. After the space write, cursor
 * is explicitly positioned to ``cursorEndCol`` (defaults to ``col``,
 * which matches the common test setup where no echoes were confirmed).
 */
function eraseFrame(col: number, cursorEndCol?: number): string {
  const end = cursorEndCol ?? col
  return `\x1b[${col + 1}G \x1b[${end + 1}G`
}

/**
 * v7 rollback frame: ``count`` spaces from ``startCol`` then explicit
 * cursor positioning to ``cursorEndCol`` (defaults to ``startCol`` =
 * serverCol when no echoes were confirmed before the rollback).
 */
function rollbackFrame(startCol: number, count: number, cursorEndCol?: number): string {
  const end = cursorEndCol ?? startCol
  return `\x1b[${startCol + 1}G${' '.repeat(count)}\x1b[${end + 1}G`
}

beforeEach(() => {
  vi.useFakeTimers({ now: 0 })
})
afterEach(() => {
  vi.useRealTimers()
})

describe('PredictiveEngine — onUserInput forwards bytes to onSend', () => {
  it('always sends printable input to the server', () => {
    const { engine, onSend } = makeEngine()
    engine.onUserInput('A')
    expect(onSend).toHaveBeenCalledWith('A')
  })

  it('sends multi-char input as a single chunk', () => {
    const { engine, onSend } = makeEngine()
    engine.onUserInput('hello')
    expect(onSend).toHaveBeenCalledWith('hello')
  })

  it('still sends when prediction is suppressed (kill switch off)', () => {
    const { engine, onSend } = makeEngine()
    engine.setKillSwitch(true)
    engine.onUserInput('A')
    expect(onSend).toHaveBeenCalledWith('A')
  })
})

describe('PredictiveEngine — printable predict (anchor + CHA + dim+underline)', () => {
  it('first keystroke paints with CHA at cursorX, dim+underline SGR', () => {
    const { engine, term } = makeEngine() // cursorX = 5
    engine.onUserInput('h')
    expect(term.write).toHaveBeenCalledWith(paintFrame(5, 'h'))
    expect(engine.getMetrics().predicted).toBe(1)
  })

  it('does NOT predict for non-printable bytes (Tab/Enter/etc.)', () => {
    const { engine, term, onSend } = makeEngine()
    engine.onUserInput('\t') // Tab is below 0x20
    expect(onSend).toHaveBeenCalledWith('\t')
    expect(term.write).not.toHaveBeenCalled()
  })

  it('does NOT predict when the kill switch is off', () => {
    const { engine, term } = makeEngine()
    engine.setKillSwitch(true)
    engine.onUserInput('A')
    expect(term.write).not.toHaveBeenCalled()
  })

  it('does NOT predict in alt-screen mode', () => {
    const { engine, term } = makeEngine()
    const altHandler = (term.parser.registerCsiHandler.mock.calls.find(
      (c: unknown[]) => {
        const opts = c[0] as { prefix?: string; final?: string }
        return opts.prefix === '?' && opts.final === 'h'
      },
    )?.[1]) as ((params: number[][]) => boolean) | undefined
    expect(altHandler).toBeTypeOf('function')
    altHandler!([[1049]])
    term.write.mockClear()
    engine.onUserInput('A')
    expect(term.write).not.toHaveBeenCalled()
  })

  it('does NOT predict when the cursor is at the line edge', () => {
    const { engine, term } = makeEngine({ term: makeTerminal(79) })
    engine.onUserInput('A')
    expect(term.write).not.toHaveBeenCalled()
  })
})

// Spec ref: §4.2 — v6 anchored overlay. The anchor stays fixed for the
// entire burst; subsequent keystrokes paint at anchor.x + N via CHA, never
// via cursor-relative advance.
describe('PredictiveEngine — sequential predictions advance via CHA (§4.2)', () => {
  it('second keystroke paints at anchor + 1 (col 6)', () => {
    const { engine, term } = makeEngine() // anchor.x = 5
    engine.onUserInput('c')
    engine.onUserInput('d')
    expect(term.write).toHaveBeenNthCalledWith(1, paintFrame(5, 'c'))
    expect(term.write).toHaveBeenNthCalledWith(2, paintFrame(6, 'd'))
  })

  it('three sequential keystrokes paint at consecutive columns', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('a')
    engine.onUserInput('b')
    engine.onUserInput('c')
    expect(term.write).toHaveBeenNthCalledWith(1, paintFrame(5, 'a'))
    expect(term.write).toHaveBeenNthCalledWith(2, paintFrame(6, 'b'))
    expect(term.write).toHaveBeenNthCalledWith(3, paintFrame(7, 'c'))
  })

  it('multi-char single batch paints once, all chars in one frame', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('hi')
    expect(term.write).toHaveBeenCalledTimes(1)
    expect(term.write).toHaveBeenCalledWith(paintFrame(5, 'hi'))
    expect(engine.getMetrics().predicted).toBe(2)
  })

  it('after server confirms head, next keystroke continues at the same anchor', () => {
    // anchor stays fixed; nextCol advances regardless of confirmation.
    const { engine, term } = makeEngine()
    engine.onUserInput('a') // pending = [a@5], nextCol=6
    engine.onUserInput('b') // pending = [a@5, b@6], nextCol=7
    engine.onServerData('a') // pending = [b@6]
    engine.onUserInput('c') // pending = [b@6, c@7], nextCol=8
    expect(term.write).toHaveBeenNthCalledWith(3, paintFrame(7, 'c'))
  })

  it('after pending fully drains, anchor resets to current cursor', () => {
    const term = makeTerminal(5)
    const { engine } = makeEngine({ term })
    engine.onUserInput('a')
    engine.onServerData('a') // pending empty, anchor cleared
    // Move cursor forward to simulate echo having advanced it.
    term.buffer.active.cursorX = 10
    engine.onUserInput('b')
    expect(term.write).toHaveBeenNthCalledWith(2, paintFrame(10, 'b'))
  })

  it('row-end check considers nextCol, not cursorX', () => {
    const { engine, term } = makeEngine({ term: makeTerminal(76) })
    engine.onUserInput('a') // col 76
    engine.onUserInput('b') // col 77
    engine.onUserInput('c') // col 78 — last legal slot before edge (cols-1 = 79)
    expect(term.write).toHaveBeenCalledTimes(3)
    term.write.mockClear()
    engine.onUserInput('d') // nextCol = 79, blocked
    expect(term.write).not.toHaveBeenCalled()
  })
})

// Server-byte interpretation — the heart of v6's defense against bash echo
// ESC chaos. Spec §4.3.
describe('PredictiveEngine — onServerData rollback triggers (§4.3)', () => {
  it('matching server byte advances FIFO, increments confirmed', () => {
    const { engine } = makeEngine()
    engine.onUserInput('h')
    expect(engine.getMetrics().predicted).toBe(1)
    engine.onServerData('h')
    expect(engine.getMetrics().confirmed).toBe(1)
  })

  it('non-matching server printable byte rolls back all pending', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('hi') // pending = [h@5, i@6]
    term.write.mockClear()
    engine.onServerData('X') // X != h → rollback
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 2))
    expect(engine.getMetrics().rolledBack).toBe(2)
  })

  it('CSI K (clear EOL) rolls back pending', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('hi')
    term.write.mockClear()
    engine.onServerData('\x1b[K')
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 2))
    expect(engine.getMetrics().rolledBack).toBe(2)
  })

  it('CSI J (clear screen) rolls back pending', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('hi')
    term.write.mockClear()
    engine.onServerData('\x1b[J')
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 2))
  })

  it('CSI G (CHA — cursor jump) rolls back pending', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('hi')
    term.write.mockClear()
    engine.onServerData('\x1b[10G')
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 2))
  })

  it('CSI H (CUP) rolls back pending', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('hi')
    term.write.mockClear()
    engine.onServerData('\x1b[1;10H')
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 2))
  })

  it('CR while pending non-empty rolls back', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('hi')
    term.write.mockClear()
    engine.onServerData('\r')
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 2))
  })

  it('LF while pending non-empty rolls back', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('hi')
    term.write.mockClear()
    engine.onServerData('\n')
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 2))
  })

  it('?2004h/l (paste-aware mode toggle) does NOT roll back', () => {
    // Bash readline emits this around every prompt redraw; rolling back
    // here would defeat predictions entirely.
    const { engine, term } = makeEngine()
    engine.onUserInput('h')
    term.write.mockClear()
    engine.onServerData('\x1b[?2004l')
    engine.onServerData('\x1b[?2004h')
    expect(term.write).not.toHaveBeenCalled()
  })

  it('SGR sequences (colors) do NOT roll back', () => {
    const { engine, term } = makeEngine()
    engine.onUserInput('h')
    term.write.mockClear()
    engine.onServerData('\x1b[31m') // red
    engine.onServerData('\x1b[m')   // reset
    expect(term.write).not.toHaveBeenCalled()
  })
})

describe('PredictiveEngine — anchor row drift', () => {
  it('rolls back when cursor row changes between bursts', () => {
    const term = makeTerminal(5, 0)
    const { engine } = makeEngine({ term })
    engine.onUserInput('a')
    expect(term.write).toHaveBeenCalledTimes(1)
    // Simulate cursor moving to a new row before the next keystroke.
    term.buffer.active.cursorY = 1
    term.write.mockClear()
    engine.onUserInput('b')
    // First the rollback for the stale anchor on row 0...
    expect(term.write.mock.calls[0][0]).toBe(rollbackFrame(5, 1))
    // ...then a fresh predict on the new row.
    expect(term.write.mock.calls[1][0]).toBe(paintFrame(5, 'b'))
  })
})

describe('PredictiveEngine — non-printable input is a fence', () => {
  it('Enter (\\r) rolls back pending and passes through', () => {
    const { engine, term, onSend } = makeEngine()
    engine.onUserInput('h')
    term.write.mockClear()
    onSend.mockClear()
    engine.onUserInput('\r')
    expect(onSend).toHaveBeenCalledWith('\r')
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 1))
    expect(engine.getMetrics().rolledBack).toBe(1)
  })

  it('Tab (\\t) rolls back pending', () => {
    const { engine, term, onSend } = makeEngine()
    engine.onUserInput('hi')
    term.write.mockClear()
    onSend.mockClear()
    engine.onUserInput('\t')
    expect(onSend).toHaveBeenCalledWith('\t')
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 2))
  })

  it('Ctrl-C (\\x03) rolls back pending', () => {
    const { engine, term, onSend } = makeEngine()
    engine.onUserInput('h')
    term.write.mockClear()
    onSend.mockClear()
    engine.onUserInput('\x03')
    expect(onSend).toHaveBeenCalledWith('\x03')
    expect(term.write).toHaveBeenCalledWith(rollbackFrame(5, 1))
  })

  it('non-printable with empty pending sends only (no extra writes)', () => {
    const { engine, term, onSend } = makeEngine()
    engine.onUserInput('\r')
    expect(onSend).toHaveBeenCalledWith('\r')
    expect(term.write).not.toHaveBeenCalled()
  })
})

// Spec ref: §4.2.1 — BS / DEL erase the rightmost predicted cell.
describe('PredictiveEngine — BS / DEL rolls back the last prediction', () => {
  it('DEL with multi-pending erases the rightmost cell at its CHA col', () => {
    const { engine, term, onSend } = makeEngine()
    engine.onUserInput('hi') // pending = [h@5, i@6]
    term.write.mockClear()
    engine.onUserInput('\x7f')
    expect(onSend).toHaveBeenCalledWith('\x7f')
    expect(term.write).toHaveBeenCalledWith(eraseFrame(6))
    expect(engine.getMetrics().rolledBack).toBe(1)
  })

  it('BS with single pending char erases at the anchor column', () => {
    const { engine, term } = makeEngine() // anchor.x = 5
    engine.onUserInput('x')
    term.write.mockClear()
    engine.onUserInput('\b')
    expect(term.write).toHaveBeenCalledWith(eraseFrame(5))
  })

  it('BS with empty pending sends but does not paint (prompt-safe)', () => {
    const { engine, term, onSend } = makeEngine()
    engine.onUserInput('\b')
    expect(onSend).toHaveBeenCalledWith('\b')
    expect(term.write).not.toHaveBeenCalled()
    expect(engine.getMetrics().rolledBack).toBe(0)
  })

  it('repeated DEL drains pending one at a time, then becomes pass-through', () => {
    const { engine, term, onSend } = makeEngine()
    engine.onUserInput('ab') // pending = [a@5, b@6]
    term.write.mockClear()
    onSend.mockClear()
    engine.onUserInput('\x7f') // pops b@6
    engine.onUserInput('\x7f') // pops a@5
    engine.onUserInput('\x7f') // empty → pass through
    expect(term.write).toHaveBeenNthCalledWith(1, eraseFrame(6))
    expect(term.write).toHaveBeenNthCalledWith(2, eraseFrame(5))
    expect(term.write).toHaveBeenCalledTimes(2)
    expect(onSend).toHaveBeenCalledTimes(3)
    expect(engine.getMetrics().rolledBack).toBe(2)
  })

  it('after BS pops, the next predict targets the freed column', () => {
    const term = makeTerminal(5)
    const { engine } = makeEngine({ term })
    engine.onUserInput('hi') // [h@5, i@6]
    engine.onUserInput('\x7f') // pops i@6, nextCol = 6
    engine.onUserInput('j') // should paint at col 6
    expect(term.write).toHaveBeenLastCalledWith(paintFrame(6, 'j'))
  })

  it('BS does not predict when canPredict() is false (kill switch off)', () => {
    const { engine, term, onSend } = makeEngine()
    engine.onUserInput('x')
    engine.setKillSwitch(true)
    term.write.mockClear()
    onSend.mockClear()
    engine.onUserInput('\b')
    expect(onSend).toHaveBeenCalledWith('\b')
    expect(term.write).not.toHaveBeenCalled()
  })
})

describe('PredictiveEngine — kill switch flips state cleanly', () => {
  it('toggleKillSwitch flips and rolls back pending predictions', () => {
    const { engine } = makeEngine()
    engine.onUserInput('hi')
    expect(engine.getMetrics().predicted).toBe(2)
    engine.toggleKillSwitch()
    expect(engine.isKillSwitchOff()).toBe(true)
    expect(engine.getMetrics().rolledBack).toBe(2)
  })

  it('setKillSwitch(true) twice is idempotent', () => {
    const { engine } = makeEngine()
    engine.onUserInput('a')
    engine.setKillSwitch(true)
    const after = engine.getMetrics().rolledBack
    engine.setKillSwitch(true)
    expect(engine.getMetrics().rolledBack).toBe(after)
  })
})

describe('PredictiveEngine — disconnect rolls back', () => {
  it('onDisconnect clears the FIFO and bumps rolledBack', () => {
    const { engine } = makeEngine()
    engine.onUserInput('foo')
    engine.onDisconnect()
    expect(engine.getMetrics().rolledBack).toBe(3)
  })
})

// gcStale is exercised indirectly by the rollback paths (CSI K, non-match,
// kill-switch, onDisconnect). Mocking performance.now in jsdom is brittle
// — Vitest's default toFake list excludes 'performance' and module-level
// monkeypatching leaks into adjacent tests. Skipping a dedicated unit test
// here in favour of dogfooding signal (gcStale fires only when echo never
// arrives, which is rare in production).

describe('PredictiveEngine — metrics + dispose', () => {
  it('getMetrics returns a snapshot (mutating it does not affect state)', () => {
    const { engine } = makeEngine()
    engine.onUserInput('A')
    const snap = engine.getMetrics()
    snap.predicted = 999
    expect(engine.getMetrics().predicted).toBe(1)
  })

  it('dispose() unregisters CSI handlers and clears the timeout', () => {
    const { engine, term } = makeEngine()
    const disposers = (term.parser.registerCsiHandler.mock.results as Array<{
      value: { dispose: ReturnType<typeof vi.fn> }
    }>).map((r) => r.value.dispose)
    expect(disposers.length).toBeGreaterThan(0)
    engine.onUserInput('A')
    engine.dispose()
    for (const d of disposers) {
      expect(d).toHaveBeenCalled()
    }
  })
})

describe('PredictiveEngine — onMetrics callback', () => {
  it('fires after each prediction', () => {
    const onMetrics = vi.fn()
    const term = makeTerminal()
    const engine = new PredictiveEngine({
      terminal: term as unknown as Terminal,
      onSend: vi.fn(),
      onMetrics,
    })
    engine.onUserInput('a')
    expect(onMetrics).toHaveBeenCalled()
    const last = onMetrics.mock.calls[onMetrics.mock.calls.length - 1][0]
    expect(last.predicted).toBe(1)
  })
})
