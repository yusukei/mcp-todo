/**
 * Workbench reducer (純関数).
 *
 * Phase B 設計書 v2.1 §4.4 の Action 駆動片方向フローの中核.
 *
 * ## 不変条件
 *
 *   - I-1  reducer は副作用を一切持たない. 永続化 / URL writeback は
 *          dispatcher 側で行う (`useWorkbenchStore`).
 *   - I-2  `system.hydrate` は lazy initializer 内でのみ呼ばれる.
 *   - I-7  `system.refreshFromServer` は state.lastUserActionAt が
 *          server updatedAt より新しい場合 silently skip
 *          (ユーザの直近変更を保護).
 *
 * v1 の `replace` / `mutate` 2 種を **action.kind による意味分類**
 * に置き換えたのが v2 の本質. 副作用を「state の変化」ではなく
 * 「dispatch された action の種類」で判定することで echo loop が
 * 構造的に発生しなくなる.
 */
import type { LayoutTree } from '../types'
import {
  addTab,
  closeTab,
  defaultLayout,
  makePane,
  moveTabToCenter,
  moveTabToEdge,
  setActiveTab,
  setSplitSizes,
  splitTabGroup,
  updatePaneConfig,
  validateTree,
} from '../treeUtils'
import { getPreset } from '../presets'
import type { Action } from './actions'

export interface State {
  tree: LayoutTree
  /**
   * 最後に user.* action を適用した wall-clock ms.
   * `system.refreshFromServer` の I-7 ガードに使う. 未操作 = 0.
   */
  lastUserActionAt: number
  /**
   * これまでに observed した最新の server updatedAt (ISO8601).
   * `useInitialServerRefresh` / SSE handler 側で trace に使う.
   * reducer 自体はこの値を比較ロジックに直接使わない (I-7 は
   * lastUserActionAt vs action.updatedAt の比較).
   */
  serverUpdatedAt: string | null
}

function parseUpdatedAtMs(iso: string): number {
  const t = Date.parse(iso)
  return Number.isFinite(t) ? t : 0
}

function applyTreeChange(
  state: State,
  next: LayoutTree,
  source: 'user' | 'remote' | 'system',
  serverUpdatedAt?: string,
): State {
  // tree 変更が **無かった** 場合は state 同一参照を返す
  // (treeUtils mutator は no-op で同じ ref を返す契約).
  const treeChanged = next !== state.tree
  if (!treeChanged) {
    if (source === 'system' && serverUpdatedAt) {
      // system.refreshFromServer で同 tree が来た場合でも
      // serverUpdatedAt は更新する (cursor 進行).
      return { ...state, serverUpdatedAt }
    }
    return state
  }
  return {
    tree: next,
    lastUserActionAt:
      source === 'user' ? Date.now() : state.lastUserActionAt,
    serverUpdatedAt:
      source === 'system' && serverUpdatedAt
        ? serverUpdatedAt
        : state.serverUpdatedAt,
  }
}

/** Defensive: validate the candidate tree; on failure log and keep
 *  the previous state. Mirrors the v1 `updateTree` guard. */
function safeApply(
  state: State,
  candidate: LayoutTree,
  source: 'user' | 'remote' | 'system',
  serverUpdatedAt?: string,
): State {
  const err = validateTree(candidate)
  if (err) {
    // eslint-disable-next-line no-console
    console.error('[Workbench reducer] refusing invalid tree:', err)
    return state
  }
  return applyTreeChange(state, candidate, source, serverUpdatedAt)
}

export function reducer(state: State, action: Action): State {
  switch (action.kind) {
    // ─ User actions ─────────────────────────────────────────
    case 'user.activateTab':
      return safeApply(
        state,
        setActiveTab(state.tree, action.groupId, action.tabId),
        'user',
      )
    case 'user.closeTab': {
      const next = closeTab(state.tree, action.groupId, action.tabId)
      return safeApply(state, next ?? defaultLayout(), 'user')
    }
    case 'user.addTab':
      return safeApply(
        state,
        addTab(state.tree, action.groupId, makePane(action.paneType)),
        'user',
      )
    case 'user.moveTab': {
      const next =
        action.drop.kind === 'edge'
          ? moveTabToEdge(
              state.tree,
              action.paneId,
              action.targetGroupId,
              action.drop.edge,
            )
          : moveTabToCenter(
              state.tree,
              action.paneId,
              action.targetGroupId,
              action.drop.index,
            )
      return safeApply(state, next, 'user')
    }
    case 'user.split':
      return safeApply(
        state,
        splitTabGroup(
          state.tree,
          action.groupId,
          action.orientation,
          action.newPaneType,
        ),
        'user',
      )
    case 'user.closeGroup': {
      // 全タブを順に close → group が collapse する
      // (closeTab の挙動: 最後のタブを閉じると group ノードが消える).
      const collectIds = (t: LayoutTree): string[] => {
        if (t.kind === 'tabs')
          return t.id === action.groupId ? t.tabs.map((p) => p.id) : []
        return t.children.flatMap(collectIds)
      }
      const tabIds = collectIds(state.tree)
      let next: LayoutTree = state.tree
      for (const id of tabIds) {
        const r = closeTab(next, action.groupId, id)
        next = r ?? defaultLayout()
      }
      return safeApply(state, next, 'user')
    }
    case 'user.splitSizes':
      return safeApply(
        state,
        setSplitSizes(state.tree, action.splitId, action.sizes),
        'user',
      )
    case 'user.configChange':
      return safeApply(
        state,
        updatePaneConfig(state.tree, action.paneId, action.patch),
        'user',
      )
    case 'user.applyPreset': {
      const preset = getPreset(action.presetId)
      if (!preset) return state
      return safeApply(state, preset.build(), 'user')
    }
    case 'user.resetLayout':
      return safeApply(state, defaultLayout(), 'user')

    // ─ Remote actions (副作用なし) ─────────────────────────
    case 'remote.serverPush':
      // LWW: server 側が新しいので tree を全置換.
      // serverUpdatedAt も同時に進める.
      return {
        ...applyTreeChange(state, action.tree, 'remote'),
        serverUpdatedAt: action.updatedAt,
      }
    case 'remote.crossTab': {
      // 別タブが書いた localStorage payload の stamp を、自タブの
      // 直近 user action の wall-clock と比較する. stamp が古い =
      // 自タブの方が新しい変更を持っている → adopt しない (LWW).
      // v1 の `if (stamp > localStampRef.current)` と同等の guard を
      // reducer 側で完結させる (dispatch 前のフィルタ層に移すと
      // store 外に state を漏らす必要が出るため).
      if (
        state.lastUserActionAt > 0 &&
        action.stamp <= state.lastUserActionAt
      ) {
        return state
      }
      return applyTreeChange(state, action.tree, 'remote')
    }

    // ─ System actions (副作用なし) ─────────────────────────
    case 'system.hydrate':
      // initialState 内でのみ呼ばれる前提だが、reducer 経由で再
      // hydrate される運用にも備える.
      return safeApply(state, action.tree, 'system')
    case 'system.refreshFromServer': {
      // I-7: 直近 user action より古い server snapshot は skip
      const serverMs = parseUpdatedAtMs(action.updatedAt)
      if (
        state.lastUserActionAt > 0 &&
        state.lastUserActionAt > serverMs
      ) {
        // ユーザ変更を保護. cursor だけ進める (再 fetch しない).
        return { ...state, serverUpdatedAt: action.updatedAt }
      }
      return safeApply(state, action.tree, 'system', action.updatedAt)
    }
  }
}
