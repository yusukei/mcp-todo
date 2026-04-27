/**
 * Workbench 永続化の dispatcher 側ヘルパ.
 *
 * v1 では `WorkbenchPage` 内に `useRef(makeDebouncedSaver(300))` /
 * `useRef(makeServerSaver(500, ...))` として持っていた saver を、
 * **module-level singleton** に格上げした.
 *
 * - **module-level の理由**: dispatcher (`useWorkbenchStore`) は
 *   StrictMode で 2 回 hook 評価される. saver を hook 内で生成すると
 *   StrictMode で別 instance が並走し、双方が独立に setTimeout を
 *   発射するため二重 PUT が起きる. module-level なら 1 個で済む.
 *
 * - **`forProject` keying**: 1 つの singleton で複数 projectId を
 *   面倒見るため、各 saver は projectId を引数に受け取る. `flush` /
 *   `cancel` は projectId 単位ではなく **直近 pending を 1 つだけ**
 *   持つ単純設計 (project 切替時はまず flush してから新しい save を
 *   開始する想定). WorkbenchPage は `key={projectId}` で remount する
 *   ので、project 切替時に `usePersistenceBeacon` の cleanup が flush
 *   を呼ぶ → 新 mount 側が新 project の save を始める = 順次安全.
 */
import {
  beaconLayout,
  makeServerSaver,
} from '../../api/workbenchLayouts'
import {
  getOrCreateClientId,
  makeDebouncedSaver,
} from '../storage'
import { LAYOUT_SCHEMA_VERSION } from '../types'
import type { LayoutTree } from '../types'

const localSaver = makeDebouncedSaver(300)
const serverSaver = makeServerSaver(500, () => getOrCreateClientId())

/** debounce 付き localStorage 保存 (300ms). */
export function saveLocalDebounced(
  projectId: string,
  tree: LayoutTree,
): void {
  localSaver.save(projectId, tree)
}

/** debounce 付き server PUT (500ms). client_id は内部で付与. */
export function saveServerDebounced(
  projectId: string,
  tree: LayoutTree,
): void {
  serverSaver.save(projectId, tree)
}

/** 即時 flush (project 切替 / unmount / visibility 変化用). */
export function flushPersistence(): void {
  localSaver.flush()
  serverSaver.flush()
}

/** 保留中の save を破棄. unmount 後の遅延 PUT を避けるとき用. */
export function cancelPersistence(): void {
  localSaver.cancel()
  serverSaver.cancel()
}

/** beforeunload / pagehide で navigator.sendBeacon に最終 layout を流す. */
export function flushBeacon(projectId: string, tree: LayoutTree): void {
  // server saver の保留分は捨てる (beacon が代替するため二重送信を避ける)
  serverSaver.cancel()
  beaconLayout(projectId, {
    tree,
    schema_version: LAYOUT_SCHEMA_VERSION,
    client_id: getOrCreateClientId(),
  })
}
