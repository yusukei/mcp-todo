/**
 * Workbench cross-pane event bus.
 *
 * Panes communicate without holding refs to each other. They emit
 * named events; the bus resolves a *target pane* by walking the
 * current LayoutTree and consulting the focus history, then fans the
 * payload out to subscribers registered for that pane id.
 *
 * Routing policy (see Phase C PR3 task description) is centralised
 * here so individual panes don't need to know about layout state:
 *
 *   1. focused pane of matching type
 *   2. most-recently-focused pane of matching type
 *   3. first pane of matching type found in the tree
 *   4. no match → toast + drop the event
 *
 * Subscribers register by ``paneId``; the bus only delivers the
 * payload to the chosen target. Broadcast (deliver to all subscribers
 * of an event) is intentionally not exposed — every cross-pane action
 * we have so far is "pick one pane and route to it", which keeps
 * unintended fan-out impossible by construction.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
} from 'react'
import type { ReactNode } from 'react'
import { showInfoToast } from '../components/common/Toast'
import type { LayoutTree, PaneType } from './types'

// ── Event catalogue ───────────────────────────────────────────

/**
 * Cross-pane event payloads. Add a new event by extending this map;
 * the routing config below decides which pane type receives it.
 */
export interface WorkbenchEventMap {
  /** Open a project document in the active DocPane. */
  'open-doc': { docId: string }
  /** Run ``cd <cwd>`` in the active TerminalPane. */
  'open-terminal-cwd': { cwd: string }
}

export type WorkbenchEventName = keyof WorkbenchEventMap

/** Which pane type owns each event. */
const EVENT_TARGET_TYPE: Record<WorkbenchEventName, PaneType> = {
  'open-doc': 'doc',
  'open-terminal-cwd': 'terminal',
}

/** Toast text shown when no pane of the right type is open. */
const NO_TARGET_MESSAGE: Record<WorkbenchEventName, string> = {
  'open-doc': 'No Doc pane is open. Add one from the + menu and try again.',
  'open-terminal-cwd':
    'No Terminal pane is open. Add one from the + menu and try again.',
}

// ── Tree helpers ──────────────────────────────────────────────

interface PaneInfo {
  id: string
  paneType: PaneType
}

/** Flatten the layout tree into a paneId → paneType lookup. The
 *  returned array is ordered by tree traversal, so consumers can use
 *  it as a deterministic "first matching pane" fallback. */
function listPanes(tree: LayoutTree): PaneInfo[] {
  if (tree.kind === 'tabs') {
    return tree.tabs.map((p) => ({ id: p.id, paneType: p.paneType }))
  }
  return tree.children.flatMap(listPanes)
}

// ── Context ───────────────────────────────────────────────────

type Listener<E extends WorkbenchEventName> = (
  payload: WorkbenchEventMap[E],
) => void

interface WorkbenchEventBus {
  emit: <E extends WorkbenchEventName>(
    event: E,
    payload: WorkbenchEventMap[E],
  ) => void
  subscribe: <E extends WorkbenchEventName>(
    paneId: string,
    event: E,
    cb: Listener<E>,
  ) => () => void
  setFocusedPane: (paneId: string) => void
}

const WorkbenchEventContext = createContext<WorkbenchEventBus | null>(null)

interface ProviderProps {
  tree: LayoutTree
  children: ReactNode
  /** Maximum size of the LRU. Beyond this, the oldest entry is
   *  forgotten when a new pane gains focus. 16 is comfortably more
   *  than ``MAX_TAB_GROUPS * MAX_TABS_PER_GROUP / 2``. */
  recentlyFocusedLimit?: number
}

export function WorkbenchEventProvider({
  tree,
  children,
  recentlyFocusedLimit = 16,
}: ProviderProps) {
  // ── Refs (mutable state that doesn't drive renders) ──────────

  /** Listeners keyed by ``paneId:eventName``. Each entry stores a Set
   *  so a pane that mounts twice (StrictMode dev) can still attach
   *  exactly one effective callback per (pane, event). */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const listenersRef = useRef<Map<string, Set<Listener<any>>>>(new Map())

  /** Current focus + LRU. Stored in a ref because we don't want a
   *  re-render every time the user clicks a pane. */
  const focusedRef = useRef<string | null>(null)
  const recentRef = useRef<string[]>([])

  /** Always-fresh tree snapshot for routing. The tree is a prop (it
   *  changes whenever the layout mutates); using a ref here is just
   *  to avoid having ``emit`` and ``subscribe`` capture a stale tree
   *  in their closure. */
  const treeRef = useRef(tree)
  useEffect(() => {
    treeRef.current = tree
  }, [tree])

  // ── Focus tracking ───────────────────────────────────────────

  const setFocusedPane = useCallback(
    (paneId: string) => {
      if (focusedRef.current === paneId) return
      focusedRef.current = paneId
      // LRU update: move (or insert) paneId at the front.
      const next = [paneId, ...recentRef.current.filter((id) => id !== paneId)]
      if (next.length > recentlyFocusedLimit) {
        next.length = recentlyFocusedLimit
      }
      recentRef.current = next
    },
    [recentlyFocusedLimit],
  )

  // Prune focus state whenever a pane is closed. We compare the
  // tree's pane ids against the recentlyFocused list and drop any id
  // that no longer exists.
  useEffect(() => {
    const live = new Set(listPanes(tree).map((p) => p.id))
    if (focusedRef.current && !live.has(focusedRef.current)) {
      focusedRef.current = null
    }
    recentRef.current = recentRef.current.filter((id) => live.has(id))
  }, [tree])

  // ── Routing ──────────────────────────────────────────────────

  const resolveTargetPane = useCallback(
    (paneType: PaneType): string | null => {
      const panes = listPanes(treeRef.current)
      const ofType = panes.filter((p) => p.paneType === paneType)
      if (ofType.length === 0) return null

      // 1. focused pane of matching type
      const focused = focusedRef.current
      if (focused && ofType.some((p) => p.id === focused)) return focused

      // 2. most-recently-focused pane of matching type
      for (const id of recentRef.current) {
        if (ofType.some((p) => p.id === id)) return id
      }

      // 3. first pane of matching type in tree order
      return ofType[0].id
    },
    [],
  )

  // ── Pub/sub ──────────────────────────────────────────────────

  const subscribe = useCallback(
    <E extends WorkbenchEventName>(
      paneId: string,
      event: E,
      cb: Listener<E>,
    ): (() => void) => {
      const key = `${paneId}:${event}`
      let set = listenersRef.current.get(key)
      if (!set) {
        set = new Set()
        listenersRef.current.set(key, set)
      }
      set.add(cb)
      return () => {
        const cur = listenersRef.current.get(key)
        if (!cur) return
        cur.delete(cb)
        if (cur.size === 0) listenersRef.current.delete(key)
      }
    },
    [],
  )

  const emit = useCallback(
    <E extends WorkbenchEventName>(
      event: E,
      payload: WorkbenchEventMap[E],
    ) => {
      const targetType = EVENT_TARGET_TYPE[event]
      const target = resolveTargetPane(targetType)
      if (!target) {
        showInfoToast(NO_TARGET_MESSAGE[event])
        return
      }
      const set = listenersRef.current.get(`${target}:${event}`)
      if (!set || set.size === 0) {
        // The chosen target pane exists in the layout but hasn't
        // mounted its subscription yet — most commonly because it's
        // sitting in an inactive tab and the tab group only mounts
        // the active child. Auto-activating that tab would require
        // the bus to know about the layout mutators; until then,
        // surface a more accurate hint than "no pane open".
        showInfoToast(
          'Target pane is on an inactive tab. Click the tab to bring it forward, then try again.',
        )
        return
      }
      for (const cb of Array.from(set)) {
        try {
          cb(payload)
        } catch (err) {
          // A listener throwing must not poison the rest. Surface
          // the error to the console so dev mode catches it.
          // eslint-disable-next-line no-console
          console.error(`[Workbench] listener for ${event} threw:`, err)
        }
      }
    },
    [resolveTargetPane],
  )

  const bus = useMemo<WorkbenchEventBus>(
    () => ({ emit, subscribe, setFocusedPane }),
    [emit, subscribe, setFocusedPane],
  )

  return (
    <WorkbenchEventContext.Provider value={bus}>
      {children}
    </WorkbenchEventContext.Provider>
  )
}

// ── Consumer hooks ────────────────────────────────────────────

export function useWorkbenchEventBus(): WorkbenchEventBus {
  const ctx = useContext(WorkbenchEventContext)
  if (!ctx) {
    throw new Error(
      'useWorkbenchEventBus must be called inside <WorkbenchEventProvider>',
    )
  }
  return ctx
}

/** Convenience: subscribe to one event for the lifetime of the
 *  calling component. The callback is wrapped in a ref so the
 *  caller's ``cb`` can close over fresh props without forcing a
 *  resubscribe on every render. */
export function useWorkbenchEvent<E extends WorkbenchEventName>(
  paneId: string | undefined,
  event: E,
  cb: Listener<E>,
): void {
  const bus = useContext(WorkbenchEventContext)
  const cbRef = useRef(cb)
  useEffect(() => {
    cbRef.current = cb
  }, [cb])
  useEffect(() => {
    if (!bus || !paneId) return
    return bus.subscribe(paneId, event, (payload) => cbRef.current(payload))
  }, [bus, paneId, event])
}
