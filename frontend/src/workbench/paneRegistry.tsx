/**
 * Single source of truth for which pane types exist and how they
 * render. Adding a new pane type means: (1) add it to ``PaneType`` in
 * ``./types``, (2) write a component, (3) register it here. Nothing
 * else in the Workbench needs to know about specific pane types.
 */
import type React from 'react'
import type { PaneType } from './types'
import TasksPane from './panes/TasksPane'
import TerminalPane from './panes/TerminalPane'
import DocPane from './panes/DocPane'
import FileBrowserPane from './panes/FileBrowserPane'
import UnsupportedPane from './panes/UnsupportedPane'

export interface PaneComponentProps {
  /** Stable pane id (Pane.id). Panes use this to register cross-pane
   *  event listeners with the workbench event bus and to identify
   *  themselves when reporting focus. */
  paneId: string
  /** The Workbench's project context. Most panes scope their queries
   *  to this. */
  projectId: string
  /** Pane-specific config persisted in the LayoutTree. Components
   *  must treat this as immutable; they call ``onConfigChange`` to
   *  request a write-back. */
  paneConfig: Record<string, unknown>
  /** Patch the persisted ``paneConfig``. Persisted via the debounced
   *  saver in WorkbenchPage. */
  onConfigChange: (patch: Record<string, unknown>) => void
}

export type PaneComponent = React.FC<PaneComponentProps>

const registry: Record<PaneType, PaneComponent> = {
  tasks: TasksPane,
  terminal: TerminalPane,
  doc: DocPane,
  'file-browser': FileBrowserPane,
  unsupported: UnsupportedPane,
}

export function getPaneComponent(type: PaneType): PaneComponent {
  return registry[type] ?? UnsupportedPane
}

/** Set of registered pane types — used by the layout loader to
 *  sanitise a stored tree that references a now-removed pane type. */
export const KNOWN_PANE_TYPES = new Set<PaneType>(
  Object.keys(registry) as PaneType[],
)

/** Display label for the tab strip + ⋮ menu. */
export const PANE_TYPE_LABELS: Record<PaneType, string> = {
  tasks: 'Tasks',
  terminal: 'Terminal',
  doc: 'Doc',
  'file-browser': 'Files',
  unsupported: 'Unknown',
}
