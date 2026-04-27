import { useCallback, useContext, useMemo } from 'react'
import { createContext } from 'react'
import ErrorBoundary from '../components/common/ErrorBoundary'
import { AlertTriangle } from 'lucide-react'
import type { Pane, PaneConfigByType, PaneType } from './types'
import { getPaneComponent, type PaneComponent } from './paneRegistry'
import { useWorkbenchEventBus } from './eventBus'

interface Props {
  pane: Pane
  projectId: string
  onConfigChange: (paneId: string, patch: Record<string, unknown>) => void
}

/**
 * Optional context exposing the active pane id to children. PaneFrame
 * is the only writer; consumers should prefer ``PaneComponentProps``
 * but a few deeply-nested children (e.g. context menus inside a pane)
 * need it without prop drilling.
 */
export const PanePaneIdContext = createContext<string | null>(null)

export function useCurrentPaneId(): string | null {
  return useContext(PanePaneIdContext)
}

/**
 * Wrap a single pane with an ErrorBoundary so a render-time crash in
 * one pane (e.g. a bad doc id throwing in DocPane) does not bring
 * down the entire Workbench. The fallback shows the error and a
 * reset hint; the user can change the pane type or close the tab to
 * recover.
 *
 * Also installs the focus tracking hook for the workbench event bus:
 * a click anywhere inside the pane (including descendants that don't
 * receive focus themselves) bubbles up via ``onMouseDown`` /
 * ``onFocusCapture`` and tells the bus this pane is the routing
 * target for cross-pane events.
 *
 * ## Phase 3 typed dispatch
 *
 * `Pane` の `paneType` で getPaneComponent → 対応する typed component
 * が返る. PaneFrame 側は `pane.paneType` を runtime で確定しているので
 * `pane.paneConfig` は対応する config 型として安全に渡せる.
 * (TS の制限で 1 行 cast を介す: registry の key 型と pane.paneType の
 * リテラル型が一致しているため runtime は安全.)
 */
export default function PaneFrame({ pane, projectId, onConfigChange }: Props) {
  const bus = useWorkbenchEventBus()

  const markFocused = useCallback(() => {
    bus.setFocusedPane(pane.id)
  }, [bus, pane.id])

  // Memoised onConfigChange wrapper. The previous inline arrow
  // (``(patch) => onConfigChange(pane.id, patch)``) had a new
  // identity on every render, which caused child useEffects whose
  // deps include onConfigChange (e.g. TerminalPane's agent rebind
  // effect) to re-fire on every parent render. That spammed
  // dispatch calls and cascaded into a flicker loop.
  const wrappedOnConfigChange = useCallback(
    (patch: Partial<PaneConfigByType[PaneType]>) =>
      onConfigChange(pane.id, patch as Record<string, unknown>),
    [onConfigChange, pane.id],
  )

  // 動的に typed component を取り出して typed paneConfig と一緒に
  // 渡す. registry の declaration が `{ [K]: PaneComponent<K> }` で
  // narrow されているため、pane.paneType と pane.paneConfig は同じ T
  // に index されている.
  const typedNode = useMemo(() => {
    // generic helper: T を runtime に握る代わりに 1 ヶ所だけ unknown
    // 経由で typed call を組む (内部 helper のため consumer に cast
    // は漏れない).
    const Component = getPaneComponent(pane.paneType) as PaneComponent
    return (
      <Component
        paneId={pane.id}
        projectId={projectId}
        paneConfig={pane.paneConfig}
        onConfigChange={wrappedOnConfigChange}
      />
    )
  }, [pane.paneType, pane.id, pane.paneConfig, projectId, wrappedOnConfigChange])

  return (
    <PanePaneIdContext.Provider value={pane.id}>
      <div
        data-pane-id={pane.id}
        // Parent (TabGroup absolute inset-0) is NOT a flex
        // container, so flex-1/min-h-0 were no-ops and child
        // panes that depended on h-full collapsed (no scroll
        // surface inside TasksPane / DocPane / etc). h-full +
        // overflow-hidden lets the pane component own its own
        // overflow strategy.
        className="h-full overflow-hidden bg-white dark:bg-gray-900 outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-inset"
        // Capture-phase focus so we win even when a descendant
        // doesn't bubble (e.g. native ``<button>`` swallowing focus
        // for accessibility). Mousedown is the secondary signal for
        // descendants that aren't focusable but get clicked (e.g. a
        // markdown rendering in DocPane).
        onFocusCapture={markFocused}
        onMouseDown={markFocused}
        // tabIndex makes the wrapper focusable so keyboard shortcuts
        // (Cmd+1..4) can route focus to it. ``-1`` keeps it out of
        // the tab sequence — the active tab button already
        // represents the pane in the regular tab order.
        tabIndex={-1}
      >
        <ErrorBoundary fallback={<PaneCrashFallback />}>
          {typedNode}
        </ErrorBoundary>
      </div>
    </PanePaneIdContext.Provider>
  )
}

function PaneCrashFallback() {
  return (
    <div className="h-full flex flex-col items-center justify-center gap-3 p-6 text-center bg-red-50 dark:bg-red-950/30">
      <AlertTriangle className="w-8 h-8 text-red-500" />
      <p className="text-sm text-red-700 dark:text-red-300 font-medium">
        This pane crashed.
      </p>
      <p className="text-xs text-red-600 dark:text-red-400 max-w-md">
        Open the browser console for the stack trace. Closing or
        switching the pane type recovers; the rest of the Workbench
        is unaffected.
      </p>
    </div>
  )
}
