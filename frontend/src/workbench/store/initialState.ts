/**
 * Workbench reducer の lazy initializer.
 *
 * Phase B 設計書 v2.1 §4.4.3 に従う. mount 時に **1 回だけ純関数で**
 * 初期 state を組む. StrictMode で 2 回呼ばれても同一結果を返すので
 * 冪等 (I-2: useRef ベースの one-shot guard 不要).
 *
 * 流れ:
 *   1. localStorage から layout を hydrate (corruption は default に fallback)
 *   2. URL の `?layout=<presetId>` があれば preset で上書き (one-shot)
 *   3. URL の `?view=docs|files|errors` 互換: 該当 pane を auto-add
 *      (Decision D4)
 *   4. URL の `?view=` (board/list/timeline) → first tasks pane に反映
 *   5. legacy `lastView:<projectId>` → viewMode 未設定の残り tasks pane に注入 (Phase 6.3)
 *   6. URL の `?task=` → first task-detail pane の paneConfig.taskId
 *   7. URL の `?doc=`  → first doc pane の paneConfig.docId
 *
 * 6/7 で対応 pane が無い場合の fallback (slide-over for ?task=) は
 * page 側で paneConfig 経由ではなく state 外で管理する (WorkbenchPage
 * の `taskFallbackId` state).
 */
import { KNOWN_PANE_TYPES } from '../paneRegistry'
import { getPreset } from '../presets'
import { loadLayout } from '../storage'
import {
  addTab,
  makePane,
  updatePaneConfig,
} from '../treeUtils'
import type { LayoutTree } from '../types'
import { isPaneOfType } from '../types'
import {
  findFirstPaneOfType,
  findFirstTabsNodeId,
  parseUrlContract,
} from '../urlContract'
import type { State } from './reducer'

const TASKS_VIEW_MODES = ['board', 'list', 'timeline'] as const
type TasksViewMode = (typeof TASKS_VIEW_MODES)[number]

function isTasksViewMode(v: unknown): v is TasksViewMode {
  return (
    typeof v === 'string' &&
    (TASKS_VIEW_MODES as readonly string[]).includes(v)
  )
}

/**
 * Phase 6.3: TasksPane の `lastView:<projectId>` legacy migration を
 * lazy initializer に吸収. v1 では TasksPane の mount useEffect で
 * `if (!persistedView) onConfigChange({ viewMode: legacy })` を
 * 行っていた (eslint-disable react-hooks/exhaustive-deps 付き). I-6
 * 「lazy initializer 以外で localStorage を読まない」を満たすため、
 * ここで一度だけ読み、`paneConfig.viewMode` 未設定の全 tasks pane に
 * 注入する.
 *
 * URL `?view=` が先に走る (initializeWorkbench 内) ため、最初の tasks
 * pane に対しては URL 値が legacy より優先される。
 */
function applyLegacyTasksViewMode(
  tree: LayoutTree,
  projectId: string,
): LayoutTree {
  let legacy: TasksViewMode | null
  try {
    const raw = window.localStorage.getItem(`lastView:${projectId}`)
    legacy = isTasksViewMode(raw) ? raw : null
  } catch {
    legacy = null
  }
  if (!legacy) return tree
  return injectTasksViewMode(tree, legacy)
}

function injectTasksViewMode(
  tree: LayoutTree,
  viewMode: TasksViewMode,
): LayoutTree {
  if (tree.kind === 'tabs') {
    let changed = false
    const tabs = tree.tabs.map((t) => {
      if (!isPaneOfType(t, 'tasks')) return t
      if (t.paneConfig.viewMode) return t
      changed = true
      return { ...t, paneConfig: { ...t.paneConfig, viewMode } }
    })
    return changed ? { ...tree, tabs } : tree
  }
  let childrenChanged = false
  const children = tree.children.map((c) => {
    const r = injectTasksViewMode(c, viewMode)
    if (r !== c) childrenChanged = true
    return r
  })
  return childrenChanged ? { ...tree, children } : tree
}

/** lazy initializer 入力. ``useReducer(reducer, args, loadInitialState)`` の形で使う. */
export interface InitialStateArgs {
  projectId: string
  /** mount 時の URLSearchParams. 同等の `.get()` を持つ任意の object でよい. */
  searchParams: { get(name: string): string | null }
}

export interface InitializeResult {
  state: State
  /** ``?task=<id>`` で task-detail pane が無く、slide-over fallback が
   *  必要な場合のみ非 null. WorkbenchPage 側で setState する. */
  taskFallbackId: string | null
  /** URL に未知 query value が含まれていたか (caller が console.warn) */
  hadUnknownValue: boolean
}

export function initializeWorkbench(args: InitialStateArgs): InitializeResult {
  const { projectId, searchParams } = args
  let tree = loadLayout(projectId, KNOWN_PANE_TYPES)
  const url = parseUrlContract(searchParams)

  // ?layout=<preset> は localStorage 由来 layout を上書き (one-shot, 永続化しない)
  if (url.layout) {
    const preset = getPreset(url.layout)
    if (preset) tree = preset.build()
    // unknown preset id は url.hadUnknownValue 経路ではなく
    // 専用 console.warn (legacy compat) — caller 側で出す.
  }

  // 廃止された ?view=docs|files|errors 互換 (Decision D4)
  if (url.legacyViewToAdd) {
    const existing = findFirstPaneOfType(tree, url.legacyViewToAdd)
    if (!existing) {
      const targetGroupId = findFirstTabsNodeId(tree)
      if (targetGroupId) {
        tree = addTab(
          tree,
          targetGroupId,
          makePane(url.legacyViewToAdd),
        )
      }
    }
  }

  if (url.view) {
    const tasksPane = findFirstPaneOfType(tree, 'tasks')
    if (tasksPane) {
      tree = updatePaneConfig(tree, tasksPane.id, { viewMode: url.view })
    }
  }

  // Phase 6.3: legacy `lastView:<projectId>` を残りの tasks pane に
  // 反映. URL `?view=` が当たった pane は viewMode 設定済なので
  // skip される (idempotent).
  tree = applyLegacyTasksViewMode(tree, projectId)

  let taskFallbackId: string | null = null
  if (url.task) {
    const detailPane = findFirstPaneOfType(tree, 'task-detail')
    if (detailPane) {
      tree = updatePaneConfig(tree, detailPane.id, { taskId: url.task })
    } else {
      taskFallbackId = url.task
    }
  }

  if (url.doc) {
    const docPane = findFirstPaneOfType(tree, 'doc')
    if (docPane) {
      tree = updatePaneConfig(tree, docPane.id, { docId: url.doc })
    }
  }

  return {
    state: {
      tree,
      lastUserActionAt: 0,
      serverUpdatedAt: null,
    },
    taskFallbackId,
    hadUnknownValue: url.hadUnknownValue,
  }
}
