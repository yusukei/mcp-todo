/**
 * Workbench mount 直後の server fetch (Phase B v2.1 §4.4.6).
 *
 * `useWorkbenchStore` の lazy initializer は localStorage hydrate
 * しか行わないため、別 PC で更新された layout を反映するためには
 * mount 後に明示的に server を取りに行く必要がある.
 *
 * この hook は projectId ごとに **1 回だけ** server GET を発射し、
 * 自タブ echo (clientId 一致) でなければ `system.refreshFromServer`
 * action を dispatch する. reducer 側で I-7 (lastUserActionAt > server
 * updatedAt なら skip) ガードがかかる.
 *
 * useEffect の正当用途 (network fetch = 外部システム同期). useRef で
 * one-shot ガードしているのは StrictMode 抑止ではなく **duplicate
 * fetch 抑止** (network call を idempotent にしておきたい).
 */
import { useEffect, useRef } from 'react'
import { getServerLayout } from '../api/workbenchLayouts'
import { getOrCreateClientId } from './storage'
import type { Action } from './store/actions'

export function useInitialServerRefresh(
  projectId: string,
  dispatch: (action: Action) => void,
): void {
  const refreshedRef = useRef<string | null>(null)

  useEffect(() => {
    if (refreshedRef.current === projectId) return
    refreshedRef.current = projectId

    const ctrl = new AbortController()
    void (async () => {
      try {
        const payload = await getServerLayout(projectId, ctrl.signal)
        if (ctrl.signal.aborted) return
        if (!payload) return
        // 自タブの echo は dispatch 前のフィルタ層で消す (I-3)
        if (payload.client_id === getOrCreateClientId()) return
        dispatch({
          kind: 'system.refreshFromServer',
          tree: payload.tree,
          updatedAt: payload.updated_at,
        })
      } catch {
        // localStorage hydrate のみで継続. 後追いで SSE が同期するか、
        // 次の user action に伴う PUT 経路で server と整合される.
      }
    })()

    return () => {
      ctrl.abort()
    }
  }, [projectId, dispatch])
}
