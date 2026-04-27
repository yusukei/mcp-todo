/**
 * State → URL writeback の dispatcher 側ヘルパ.
 *
 * Phase B 設計書 v2.1 §4.4.3 に従う. v1 では state.tree 変更を
 * useEffect で監視して URL を書き戻していた (双方向同期 → render loop
 * を `searchParamsEqual` で抑止する設計) が、v2 では **user action を
 * dispatch した直後に同期的に書き戻す** ことで loop が構造的に発生
 * しなくなる.
 *
 * URL contract (Phase C2 D3 / docs/api/url-contract.md) のうち、
 * 個人 layout を表す `?view=` / `?layout=` / `?group=` は v2.6 設計で
 * 削除予定だが、本タスクでは既存挙動 (現状の serialiseUrlContract)
 * を維持する. URL S5 で contract が縮約されたらこのモジュールも
 * 縮約する.
 */
import type { ViewName } from '../urlContract'
import {
  findFirstPaneOfType,
  searchParamsEqual,
  serialiseUrlContract,
} from '../urlContract'
import type { State } from './reducer'

type SetSearchParams = (
  next: URLSearchParams,
  options?: { replace?: boolean },
) => void

/** state.tree から URL contract に同期されるべき param 集合を計算. */
function computeDesiredParams(state: State): {
  task: string | null
  doc: string | null
  view: ViewName | null
} {
  const detailPane = findFirstPaneOfType(state.tree, 'task-detail')
  const docPane = findFirstPaneOfType(state.tree, 'doc')
  const tasksPane = findFirstPaneOfType(state.tree, 'tasks')
  const desiredTask =
    (detailPane?.paneConfig as { taskId?: string } | undefined)?.taskId ?? null
  const desiredDoc =
    (docPane?.paneConfig as { docId?: string } | undefined)?.docId ?? null
  const rawView = (
    tasksPane?.paneConfig as { viewMode?: string } | undefined
  )?.viewMode
  // board は実装上の default なので URL に出さない (clean URL)
  const desiredView: ViewName | null =
    rawView === 'list' || rawView === 'timeline' ? rawView : null
  return { task: desiredTask, doc: desiredDoc, view: desiredView }
}

/**
 * user action 後に呼ばれる. tree から URL を導出し、現状と diff が
 * あれば `setSearchParams(..., { replace: true })`. 同一なら no-op.
 *
 * 戻り値: 実際に URL を書き換えた場合 true.
 */
export function syncUrlFromState(
  state: State,
  current: URLSearchParams,
  setSearchParams: SetSearchParams,
): boolean {
  const desired = computeDesiredParams(state)
  const next = serialiseUrlContract(current, desired)
  if (searchParamsEqual(current, next)) return false
  setSearchParams(next, { replace: true })
  return true
}
