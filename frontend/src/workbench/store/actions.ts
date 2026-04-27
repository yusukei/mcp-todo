/**
 * Workbench Action 型定義 (discriminated union).
 *
 * Phase B 設計書 v2.1 §4.4.2 に従う. Action は 3 系統:
 *
 *   - `user.*`   ユーザ操作起点. dispatcher の副作用ハンドラが
 *                localStorage / server PUT / URL writeback をトリガする.
 *   - `remote.*` 外部システム由来 (SSE / cross-tab). 副作用なし
 *                = echo loop が **構造的に発生しない** (I-1).
 *   - `system.*` 内部用途 (lazy initializer, mount-time server refresh).
 *                副作用なし.
 *
 * `action.kind.startsWith('user.')` だけで分岐する設計なので、
 * `useWorkbenchStore.dispatch` は **action.kind 判定のみで副作用を
 * 起動する**. reducer は純関数で副作用を一切持たない.
 */
import type { LayoutTree, PaneType } from '../types'
import type { DropEdge } from '../treeUtils'

// ── User actions ──────────────────────────────────────────────

export type UserAction =
  | { kind: 'user.activateTab'; groupId: string; tabId: string }
  | { kind: 'user.closeTab'; groupId: string; tabId: string }
  | { kind: 'user.addTab'; groupId: string; paneType: PaneType }
  | {
      kind: 'user.moveTab'
      paneId: string
      targetGroupId: string
      drop:
        | { kind: 'edge'; edge: DropEdge }
        | { kind: 'center'; index: number }
    }
  | {
      kind: 'user.split'
      groupId: string
      orientation: 'horizontal' | 'vertical'
      newPaneType: PaneType
    }
  | { kind: 'user.closeGroup'; groupId: string }
  | { kind: 'user.splitSizes'; splitId: string; sizes: number[] }
  | {
      kind: 'user.configChange'
      paneId: string
      patch: Record<string, unknown>
    }
  | { kind: 'user.applyPreset'; presetId: string }
  | { kind: 'user.resetLayout' }

// ── External (remote) actions ─────────────────────────────────

export type RemoteAction =
  | { kind: 'remote.serverPush'; tree: LayoutTree; updatedAt: string }
  | { kind: 'remote.crossTab'; tree: LayoutTree; stamp: number }

// ── System actions ────────────────────────────────────────────

export type SystemAction =
  /** 初回 hydrate 用. lazy initializer 内でのみ生成される (I-2). */
  | { kind: 'system.hydrate'; tree: LayoutTree }
  /**
   * mount 直後の server fetch 結果を反映する (Phase B v2.1 §4.4.6).
   * 直近 user action よりも server `updatedAt` が古い場合は
   * silently skip される (I-7).
   */
  | {
      kind: 'system.refreshFromServer'
      tree: LayoutTree
      updatedAt: string
    }

export type Action = UserAction | RemoteAction | SystemAction

/** 型ガード: user 由来 action のみ true. dispatcher の副作用分岐で使用. */
export function isUserAction(action: Action): action is UserAction {
  return action.kind.startsWith('user.')
}
