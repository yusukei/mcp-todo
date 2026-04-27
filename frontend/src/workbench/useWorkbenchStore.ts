/**
 * Workbench reducer + dispatcher を 1 hook にまとめた API.
 *
 * Phase B 設計書 v2.1 §4.4.3 の `useWorkbenchStore` 実装.
 *
 * ## 設計
 *
 * - **lazy initializer** で初期 state を構築 (StrictMode 冪等).
 * - **dispatch** は `action.kind` で副作用を分岐:
 *     - user.* → reducer 後の next.tree を localStorage / server に
 *                debounced save、URL に同期書き戻し.
 *     - remote.* / system.* → 副作用なし (echo loop 構造防止).
 * - **stable identity**: `dispatch` の identity が変わらないよう
 *   `stateRef` / `searchParamsRef` 経由で stale closure を避けつつ
 *   `useCallback([projectId], ...)` で固定する.
 *
 * ## 戻り値
 *
 *   - `state`              最新 reducer state
 *   - `dispatch`           安定 callback (Action を受ける)
 *   - `taskFallbackId`     ?task=<id> hydrate で task-detail pane が
 *                           無かった場合の fallback 用 task id (null
 *                           可). page 側で slide-over 表示.
 *   - `setTaskFallbackId`  fallback の制御用.
 *   - `clearTaskFallback`  ショートカット (= setTaskFallbackId(null)).
 */
import { useCallback, useReducer, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Action } from './store/actions'
import { isUserAction } from './store/actions'
import { initializeWorkbench } from './store/initialState'
import {
  saveLocalDebounced,
  saveServerDebounced,
} from './store/persistence'
import { reducer, type State } from './store/reducer'
import { syncUrlFromState } from './store/urlSync'

interface UseWorkbenchStoreReturn {
  state: State
  dispatch: (action: Action) => void
  taskFallbackId: string | null
  clearTaskFallback: () => void
  setTaskFallbackId: (id: string | null) => void
}

/**
 * `WorkbenchPage` の中核 hook.
 *
 * `projectId` は **mount 時に固定** とする (project 切替時は親で
 * `key={projectId}` を付けて remount すること). 同 hook 内で projectId
 * 変化に対応するロジックは持たない.
 */
export function useWorkbenchStore(projectId: string): UseWorkbenchStoreReturn {
  const [searchParams, setSearchParams] = useSearchParams()

  // 最新参照を ref で保持して dispatch を安定させる
  const searchParamsRef = useRef(searchParams)
  searchParamsRef.current = searchParams
  const setSearchParamsRef = useRef(setSearchParams)
  setSearchParamsRef.current = setSearchParams

  // ── lazy initialize ──────────────────────────────────────
  // useRef 経由で StrictMode 二重評価でも初期化を 1 回に抑える.
  // (useReducer の lazy initializer は state しか返せないので、
  //  taskFallbackId / hadUnknownValue を一緒に取り出すために自前で
  //  initRef にキャッシュする.)
  const initRef = useRef<{
    state: State
    taskFallbackId: string | null
  } | null>(null)
  if (initRef.current === null) {
    const init = initializeWorkbench({ projectId, searchParams })
    if (init.hadUnknownValue) {
      // eslint-disable-next-line no-console
      console.warn(
        '[Workbench] URL contained unknown query value(s); using defaults',
      )
    }
    initRef.current = {
      state: init.state,
      taskFallbackId: init.taskFallbackId,
    }
  }

  const [reducerState, dispatchRaw] = useReducer(
    reducer,
    initRef.current.state,
  )
  const [taskFallbackId, setTaskFallbackId] = useState<string | null>(
    initRef.current.taskFallbackId,
  )

  // 最新 state を closure stale させずに副作用ハンドラに渡す.
  const stateRef = useRef(reducerState)
  stateRef.current = reducerState

  // 注: dispatch の identity を完全 stable に保つため、最新値は
  //     ref 経由で参照する (古典的 stable callback パターン).
  const dispatch = useCallback(
    (action: Action) => {
      // 1. reducer (純関数) で次の state を計算
      const next = reducer(stateRef.current, action)

      // 2. state を更新
      dispatchRaw(action)

      // 3. action.kind で副作用を分岐 (Phase B 設計 v2.1 §4.4.3)
      if (isUserAction(action)) {
        // tree が変わっていない (no-op mutator) ときは save しない
        if (next.tree === stateRef.current.tree) return
        saveLocalDebounced(projectId, next.tree)
        saveServerDebounced(projectId, next.tree)
        syncUrlFromState(
          next,
          searchParamsRef.current,
          setSearchParamsRef.current,
        )
      }
    },
    [projectId],
  )

  const clearTaskFallback = useCallback(() => setTaskFallbackId(null), [])

  return {
    state: reducerState,
    dispatch,
    taskFallbackId,
    clearTaskFallback,
    setTaskFallbackId,
  }
}
