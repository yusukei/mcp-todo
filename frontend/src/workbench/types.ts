/**
 * Workbench layout types.
 *
 * The layout is a tree where every leaf is a "tab group" (one or more
 * panes shown as tabs in the same area) and inner nodes are
 * splits (horizontal or vertical) of two or more children.
 *
 * Every node carries a stable ``id`` (UUID) so React-resizable-panels
 * can track per-node layout state across re-renders, and so DnD
 * targeting (PR3.5) can address a node without relying on tree
 * traversal indices.
 *
 * ## paneConfig discriminated union (Refactor Phase 3)
 *
 * v1 では ``paneConfig: Record<string, unknown>`` の stringly-typed
 * bag だったが、v2 (Phase 3) で **paneType ごとに専用 shape を持つ
 * discriminated union** に変えた. これにより:
 *
 *   - 各 pane component が `Pane<'tasks'>` のように specific 型を受け
 *     `paneConfig.viewMode` のような直接アクセスが型安全になる
 *   - Obsidian Phase 1 F1 (`folderPath` 追加) のような拡張も union を
 *     1 行追加するだけで済む
 *   - cast (`as { ... }`) は本ファイルの内部 helper のみに局所化
 *
 * 拡張時の注意:
 *   - 新しい pane type を追加する → ``PaneType`` に追加 + ``PaneConfigByType``
 *     に対応する config interface を追加 + ``paneRegistry.tsx`` に component を
 *     登録
 *   - 既存 pane の config 拡張 → ``PaneConfigByType[<type>]`` に optional プロ
 *     パティを追加 (既存 layout JSON との後方互換のため必ず ``?:``)
 */

export type PaneType =
  | 'tasks'
  | 'task-detail'
  | 'terminal'
  | 'doc'
  | 'documents'
  | 'file-browser'
  | 'error-tracker'
  | 'unsupported'

// ── per-type config shapes ────────────────────────────────────

export interface TasksPaneConfig {
  /** TasksPane の表示モード. localStorage `lastView:<projectId>` 経由
   *  でも復元されるが、layout の paneConfig が優先 (Phase B v2.1). */
  viewMode?: 'board' | 'list' | 'timeline'
}

export interface TaskDetailPaneConfig {
  /** 表示中の task id. URL `?task=<id>` でも初期化される. */
  taskId?: string
}

export interface DocPaneConfig {
  /** 表示中の document id. URL `?doc=<id>` でも初期化される. */
  docId?: string
}

export interface DocumentsPaneConfig {
  /**
   * 選択中のドキュメント id. 既存 persisted layout との後方互換を
   * 保つため `docId` のまま (Phase 3 計画書では `selectedId` と記載
   * されていたが、production code が `docId` を使用しているので
   * リネームは別マイグレーションとする).
   */
  docId?: string
  /** Obsidian Phase 1 F1 で追加予定の folder filter (`/_root` などの
   *  仮想 path). 未指定なら全件表示. */
  folderPath?: string
}

export interface TerminalPaneConfig {
  /** 接続先 agent id. layout sync 経由で別デバイスからも継続される. */
  agentId?: string
  /** 既存セッション再アタッチ用. agentId とペア. */
  sessionId?: string
}

export interface FileBrowserPaneConfig {
  /** project.remote.remote_path からの相対 path. */
  cwd?: string
  /** 旧称: 後方互換 (古い layout JSON が `path` を持っているケース). */
  path?: string
}

export interface ErrorTrackerPaneConfig {
  /** 表示中の issue id. */
  issueId?: string
}

export interface UnsupportedPaneConfig {
  /** sanitize 時に元 paneType を保存しておく (デバッグ用). */
  originalType?: string
}

/** paneType ごとの config 型対応表. ``Pane<T>`` で内部参照される. */
export interface PaneConfigByType {
  tasks: TasksPaneConfig
  'task-detail': TaskDetailPaneConfig
  terminal: TerminalPaneConfig
  doc: DocPaneConfig
  documents: DocumentsPaneConfig
  'file-browser': FileBrowserPaneConfig
  'error-tracker': ErrorTrackerPaneConfig
  unsupported: UnsupportedPaneConfig
}

/** すべての paneConfig 型の union. Pane<PaneType>.paneConfig はこの union. */
export type PaneConfig = PaneConfigByType[PaneType]

// ── Pane / Tree ───────────────────────────────────────────────

/**
 * Pane (= タブの単位).
 *
 * 通常は `Pane` (= `Pane<PaneType>`) で扱い、specific 型が必要な箇所
 * (pane component の props など) で `Pane<'task-detail'>` のように
 * narrow する.
 *
 * 型ガード `isPaneOfType` を使うと runtime で paneType を判定して
 * narrow した型を得られる.
 */
export interface Pane<T extends PaneType = PaneType> {
  /** Stable UUID — distinct from the LayoutTree node id. */
  id: string
  paneType: T
  /**
   * Pane-type-specific configuration. paneType と同期してナローイ
   * ングされる (discriminated union).
   */
  paneConfig: PaneConfigByType[T]
}

/** runtime 型ガード. */
export function isPaneOfType<T extends PaneType>(
  pane: Pane,
  type: T,
): pane is Pane<T> {
  return pane.paneType === type
}

export interface SplitNode {
  id: string
  kind: 'split'
  orientation: 'horizontal' | 'vertical'
  children: LayoutTree[]
  /** 0..100 percentages summing (approximately) to 100. */
  sizes: number[]
}

export interface TabsNode {
  id: string
  kind: 'tabs'
  tabs: Pane[]
  /** Must reference the ``id`` of one of ``tabs``. */
  activeTabId: string
}

export type LayoutTree = SplitNode | TabsNode

/** Wire format persisted to localStorage. */
export interface PersistedLayout {
  version: 1
  /** Wall-clock ms when the layout was last written, used for
   *  last-write-wins reconciliation across browser tabs. */
  savedAt: number
  tree: LayoutTree
}

/** Hard caps to keep the UI sane. */
export const MAX_TAB_GROUPS = 4
export const MAX_TABS_PER_GROUP = 8

/** Schema version of the persisted JSON. Bump on incompatible
 *  changes; the loader falls back to the default layout when the
 *  version doesn't match. */
export const LAYOUT_SCHEMA_VERSION = 1
