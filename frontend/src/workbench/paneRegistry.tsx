/**
 * Single source of truth for which pane types exist and how they
 * render. Adding a new pane type means: (1) add it to ``PaneType`` in
 * ``./types``, (2) write a component, (3) register it here. Nothing
 * else in the Workbench needs to know about specific pane types.
 *
 * ## Phase 3 (discriminated union 化) 後の型契約
 *
 * `PaneComponentProps<T>` は `paneType T` で narrow された **typed
 * paneConfig** を受け取る. `getPaneComponent('tasks')` は
 * `React.FC<PaneComponentProps<'tasks'>>` を返すので、`PaneFrame` が
 * paneType ごとに分岐すれば実装側 (TasksPane など) は cast 不要で
 * `paneConfig.viewMode` 等を直接アクセスできる.
 *
 * 内部 registry の型アサーションは一度だけここで完結し、consumer
 * 側に cast を漏らさない (Phase 3 KPI: cast 0 箇所).
 */
import type React from 'react'
import type { PaneConfigByType, PaneType } from './types'
import TasksPane from './panes/TasksPane'
import TaskDetailPane from './panes/TaskDetailPane'
import TerminalPane from './panes/TerminalPane'
import DocPane from './panes/DocPane'
import DocumentsPane from './panes/DocumentsPane'
import FileBrowserPane from './panes/FileBrowserPane'
import ErrorTrackerPane from './panes/ErrorTrackerPane'
import UnsupportedPane from './panes/UnsupportedPane'

export interface PaneComponentProps<T extends PaneType = PaneType> {
  /** Stable pane id (Pane.id). Panes use this to register cross-pane
   *  event listeners with the workbench event bus and to identify
   *  themselves when reporting focus. */
  paneId: string
  /** The Workbench's project context. Most panes scope their queries
   *  to this. */
  projectId: string
  /** Pane-specific config persisted in the LayoutTree, narrowed to
   *  this pane type's shape (Phase 3 discriminated union). */
  paneConfig: PaneConfigByType[T]
  /** Patch the persisted ``paneConfig``. Persisted via the debounced
   *  saver in WorkbenchPage. */
  onConfigChange: (patch: Partial<PaneConfigByType[T]>) => void
}

export type PaneComponent<T extends PaneType = PaneType> = React.FC<
  PaneComponentProps<T>
>

/** Internal: paneType → typed component map. The cast lives here only;
 *  consumers of `getPaneComponent` get a fully-typed callback. */
const registry: { [K in PaneType]: PaneComponent<K> } = {
  tasks: TasksPane,
  'task-detail': TaskDetailPane,
  terminal: TerminalPane,
  doc: DocPane,
  documents: DocumentsPane,
  'file-browser': FileBrowserPane,
  'error-tracker': ErrorTrackerPane,
  unsupported: UnsupportedPane,
}

export function getPaneComponent<T extends PaneType>(
  type: T,
): PaneComponent<T> {
  return registry[type]
}

/**
 * Pane types whose component holds a long-lived connection (WebSocket,
 * PTY, SSE subscription) and must NOT be unmounted on tab switch.
 * TabGroup renders these with ``display: none`` when they are not the
 * active tab (Decision D11 / Invariant L3).
 */
const KEEP_ALIVE_PANE_TYPES = new Set<PaneType>(['terminal'])

export function isKeepAlivePane(type: PaneType): boolean {
  return KEEP_ALIVE_PANE_TYPES.has(type)
}

/** Set of registered pane types — used by the layout loader to
 *  sanitise a stored tree that references a now-removed pane type. */
export const KNOWN_PANE_TYPES = new Set<PaneType>(
  Object.keys(registry) as PaneType[],
)

/** Display label for the tab strip + ⋮ menu. */
export const PANE_TYPE_LABELS: Record<PaneType, string> = {
  tasks: 'Tasks',
  'task-detail': 'Task Detail',
  terminal: 'Terminal',
  doc: 'Doc',
  documents: 'Documents',
  'file-browser': 'Files',
  'error-tracker': 'Errors',
  unsupported: 'Unknown',
}
