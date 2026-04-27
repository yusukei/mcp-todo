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
 *   5. URL の `?task=` → first task-detail pane の paneConfig.taskId
 *   6. URL の `?doc=`  → first doc pane の paneConfig.docId
 *
 * 5/6 で対応 pane が無い場合の fallback (slide-over for ?task=) は
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
import {
  findFirstPaneOfType,
  findFirstTabsNodeId,
  parseUrlContract,
} from '../urlContract'
import type { State } from './reducer'

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
