import { useCallback, useContext } from 'react'
import { createContext } from 'react'
import ErrorBoundary from '../components/common/ErrorBoundary'
import { AlertTriangle } from 'lucide-react'
import type { Pane } from './types'
import { getPaneComponent } from './paneRegistry'
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
 */
export default function PaneFrame({ pane, projectId, onConfigChange }: Props) {
  const Component = getPaneComponent(pane.paneType)
  const bus = useWorkbenchEventBus()

  const markFocused = useCallback(() => {
    bus.setFocusedPane(pane.id)
  }, [bus, pane.id])

  return (
    <PanePaneIdContext.Provider value={pane.id}>
      <div
        className="flex-1 min-h-0 overflow-hidden bg-white dark:bg-gray-900 outline-none"
        // Capture-phase focus so we win even when a descendant
        // doesn't bubble (e.g. native ``<button>`` swallowing focus
        // for accessibility). Mousedown is the secondary signal for
        // descendants that aren't focusable but get clicked (e.g. a
        // markdown rendering in DocPane).
        onFocusCapture={markFocused}
        onMouseDown={markFocused}
        // tabIndex makes the wrapper focusable so keyboard users can
        // route events to it as well; -1 keeps it out of the tab
        // sequence (the active tab button already represents the
        // pane in the tab order).
        tabIndex={-1}
      >
        <ErrorBoundary fallback={<PaneCrashFallback />}>
          <Component
            paneId={pane.id}
            projectId={projectId}
            paneConfig={pane.paneConfig}
            onConfigChange={(patch) => onConfigChange(pane.id, patch)}
          />
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
