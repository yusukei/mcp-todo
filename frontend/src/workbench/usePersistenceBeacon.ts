/**
 * tab close / hide 時に最新 layout を server へ flush する hook.
 *
 * v1 では `WorkbenchPage` 内の `useEffect(visibilitychange / pagehide)`
 * を分離した. dispatch 経由の永続化は debounced だが、tab が閉じる
 * タイミングで debounce timer を待つ余裕は無いため `navigator.
 * sendBeacon` で同期送出する.
 *
 * useEffect の正当用途 (window event subscribe = 外部システム同期).
 *
 * `lastUserActionAt === 0` (ユーザが何も触っていない) の場合は
 * server 側が既に authoritative なので flush しない.
 */
import { useEffect } from 'react'
import { flushBeacon } from './store/persistence'
import type { State } from './store/reducer'

export function usePersistenceBeacon(
  projectId: string,
  state: State,
): void {
  useEffect(() => {
    const flushNow = () => {
      if (state.lastUserActionAt === 0) return
      flushBeacon(projectId, state.tree)
    }
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') flushNow()
    }
    const onPageHide = () => flushNow()
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', onPageHide)
    }
  }, [projectId, state.lastUserActionAt, state.tree])
}
