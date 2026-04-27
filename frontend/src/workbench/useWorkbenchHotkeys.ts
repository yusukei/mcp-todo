/**
 * Workbench keyboard shortcuts.
 *
 * v1 では `WorkbenchPage` 内に直接書かれていた `useEffect(handleKey)`
 * を専用 hook に切り出した. 動作は v1 同等:
 *   - Cmd/Ctrl + W              focused pane を閉じる
 *   - Cmd/Ctrl + \              vertical split
 *   - Cmd/Ctrl + Shift + \      horizontal split
 *   - Cmd/Ctrl + Shift + R      layout reset (確認モーダル経由)
 *   - Cmd/Ctrl + 1..4           N 番目の pane に focus
 *
 * v1 → v2 の差分:
 *   - 直接 `updateTree` を呼んでいた箇所を `dispatch(user.*)` に統一.
 *   - reset 確認モーダル開閉は呼び出し元 (page) の state に委譲する
 *     ため `onConfirmReset` callback で通知する.
 */
import { useEffect } from 'react'
import {
  dfsPanes,
  findGroupIdOf,
  focusIndex,
  focusPaneFrame,
  matchHotkey,
  resolveFocusedPaneId,
} from './hotkeys'
import type { Action } from './store/actions'
import type { LayoutTree } from './types'

export interface HotkeyDeps {
  tree: LayoutTree
  dispatch: (action: Action) => void
  /** Cmd+Shift+R 押下時に呼ばれる (page 側で確認モーダルを開く). */
  onResetLayoutRequested: () => void
}

export function useWorkbenchHotkeys(deps: HotkeyDeps): void {
  // 最新値を closure 経由で安全に参照するため、useEffect の毎回再
  // 登録 cost を避けつつ tree / dispatch / callback の identity 変化に
  // 追随する設計. ここでは hotkey の発火頻度が低い前提で素直に
  // 全部 deps に並べる.
  const { tree, dispatch, onResetLayoutRequested } = deps
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const hk = matchHotkey(e)
      if (!hk) return
      const focusedPaneId = resolveFocusedPaneId()

      if (hk === 'reset-layout') {
        e.preventDefault()
        onResetLayoutRequested()
        return
      }

      const fIdx = focusIndex(hk)
      if (fIdx !== null) {
        e.preventDefault()
        const panes = dfsPanes(tree)
        const target = panes[fIdx - 1]
        if (!target) return
        const groupId = findGroupIdOf(tree, target.id)
        if (groupId) {
          dispatch({
            kind: 'user.activateTab',
            groupId,
            tabId: target.id,
          })
        }
        window.requestAnimationFrame(() => focusPaneFrame(target.id))
        return
      }

      if (!focusedPaneId) return
      const groupId = findGroupIdOf(tree, focusedPaneId)
      if (!groupId) return

      if (hk === 'close-pane') {
        e.preventDefault()
        dispatch({
          kind: 'user.closeTab',
          groupId,
          tabId: focusedPaneId,
        })
        return
      }
      if (hk === 'split-vertical' || hk === 'split-horizontal') {
        e.preventDefault()
        const focusedPane = dfsPanes(tree).find(
          (p) => p.id === focusedPaneId,
        )
        const orientation =
          hk === 'split-vertical' ? 'vertical' : 'horizontal'
        dispatch({
          kind: 'user.split',
          groupId,
          orientation,
          newPaneType: focusedPane?.paneType ?? 'tasks',
        })
        return
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [tree, dispatch, onResetLayoutRequested])
}
