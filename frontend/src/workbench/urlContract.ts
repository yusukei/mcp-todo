/**
 * URL state contract for `/projects/:projectId` (Phase C2 D3).
 *
 * Parses + serializes the query params we care about (`?task=`,
 * `?doc=`, `?view=`, `?layout=`, `?group=`) and surfaces a small
 * "legacy compat" hint for the old ProjectPage view names
 * (`docs`/`files`/`errors`) — Decision D4 wants those to add the
 * matching pane and surface a one-shot toast.
 *
 * Push/replace policy lives at the call site (selection = replace,
 * preset apply = push — see Plan v2.4 §5.5.3). This module is pure
 * data + validation; no side effects.
 */
import type { LayoutTree, Pane, PaneType, TabsNode } from './types'

export type ViewName = 'board' | 'list' | 'timeline'

const VIEW_NAMES: readonly ViewName[] = ['board', 'list', 'timeline']

/** Legacy ProjectPage view names that don't map to a viewMode. They
 *  represent panes the old single-page UI rendered as a "view"
 *  switch; in the Workbench era they map to dedicated pane types
 *  added on the fly (Decision D4). */
const LEGACY_VIEW_TO_PANE_TYPE: Record<string, PaneType> = {
  docs: 'documents',
  files: 'file-browser',
  errors: 'error-tracker',
}

export interface UrlContract {
  /** TaskDetail target task id (`?task=`). */
  task: string | null
  /** DocPane target doc id (`?doc=`). */
  doc: string | null
  /** TasksPane viewMode (`?view=board|list|timeline`). */
  view: ViewName | null
  /** Preset id to apply once on mount (`?layout=`). Not echoed back
   *  to URL by state changes; user keeps the preset URL only as
   *  long as they don't mutate the layout further. */
  layout: string | null
  /** Timeline groupBy (`?group=`). Currently parsed but not wired —
   *  TaskTimeline accepts groupBy via prop and TasksPane could
   *  surface it later. Kept here so ?group= URLs aren't classified
   *  as "unknown query". */
  group: string | null
  /** When the URL had a legacy `?view=docs/files/errors`, this
   *  carries the pane type to auto-add (Decision D4). The caller
   *  shows a one-shot toast and adds a pane of that type if the
   *  layout doesn't already include one. */
  legacyViewToAdd: PaneType | null
  /** True when an unknown query value was seen (rolled into
   *  default). Caller can `console.warn` once per mount. */
  hadUnknownValue: boolean
}

/**
 * Parse a `URLSearchParams` (or anything with `.get()`) into the
 * typed contract. Unknown values fall back to `null` so the rest of
 * the system never sees garbage — Plan v2.4 §5.5.1.
 */
export function parseUrlContract(
  params: { get(name: string): string | null },
): UrlContract {
  let hadUnknownValue = false

  const rawTask = params.get('task')
  const task = rawTask && rawTask.trim().length > 0 ? rawTask.trim() : null

  const rawDoc = params.get('doc')
  const doc = rawDoc && rawDoc.trim().length > 0 ? rawDoc.trim() : null

  const rawView = params.get('view')
  let view: ViewName | null = null
  let legacyViewToAdd: PaneType | null = null
  if (rawView) {
    if ((VIEW_NAMES as readonly string[]).includes(rawView)) {
      view = rawView as ViewName
    } else if (LEGACY_VIEW_TO_PANE_TYPE[rawView]) {
      legacyViewToAdd = LEGACY_VIEW_TO_PANE_TYPE[rawView]
    } else {
      hadUnknownValue = true
    }
  }

  const rawLayout = params.get('layout')
  const layout = rawLayout && rawLayout.trim().length > 0 ? rawLayout.trim() : null

  const rawGroup = params.get('group')
  const group = rawGroup && rawGroup.trim().length > 0 ? rawGroup.trim() : null

  return { task, doc, view, layout, group, legacyViewToAdd, hadUnknownValue }
}

/**
 * Apply state-derived values (taskId / docId / viewMode) to a
 * URLSearchParams clone, returning the new params object. Caller
 * decides how to push it (replace vs push). Unset (null) values
 * remove the corresponding query param.
 */
export function serialiseUrlContract(
  current: URLSearchParams,
  patch: { task?: string | null; doc?: string | null; view?: ViewName | null },
): URLSearchParams {
  const next = new URLSearchParams(current)
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue
    if (value === null || value === '') {
      next.delete(key)
    } else {
      next.set(key, value)
    }
  }
  return next
}

/** Walk the tree and return the first pane (DFS, reading order)
 *  whose paneType matches. ``null`` when none exist. Used for
 *  routing URL params to a "single representative pane" without
 *  needing focus/LRU state. */
export function findFirstPaneOfType(
  tree: LayoutTree,
  paneType: PaneType,
): Pane | null {
  if (tree.kind === 'tabs') {
    return tree.tabs.find((p) => p.paneType === paneType) ?? null
  }
  for (const child of tree.children) {
    const r = findFirstPaneOfType(child, paneType)
    if (r) return r
  }
  return null
}

/** First tab group id in DFS order. Used as the auto-add target
 *  for ``?view=docs/files/errors`` legacy compat (Decision D4). */
export function findFirstTabsNodeId(tree: LayoutTree): string | null {
  if (tree.kind === 'tabs') return tree.id
  for (const child of tree.children) {
    const r = findFirstTabsNodeId(child)
    if (r) return r
  }
  return null
}

/** Tab group lookup by paneId — caller already has the pane id and
 *  needs the enclosing group to drive ``addTab``. */
export function findTabsNodeContaining(
  tree: LayoutTree,
  paneId: string,
): TabsNode | null {
  if (tree.kind === 'tabs') {
    return tree.tabs.some((p) => p.id === paneId) ? tree : null
  }
  for (const child of tree.children) {
    const r = findTabsNodeContaining(child, paneId)
    if (r) return r
  }
  return null
}

/** True when two URLSearchParams describe the same key/value set. */
export function searchParamsEqual(a: URLSearchParams, b: URLSearchParams): boolean {
  const aEntries = [...a.entries()].sort(([k1], [k2]) => k1.localeCompare(k2))
  const bEntries = [...b.entries()].sort(([k1], [k2]) => k1.localeCompare(k2))
  if (aEntries.length !== bEntries.length) return false
  return aEntries.every(([k, v], i) => bEntries[i][0] === k && bEntries[i][1] === v)
}
