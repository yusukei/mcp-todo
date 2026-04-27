/**
 * Workbench reducer の lazy initializer (initializeWorkbench).
 *
 * Phase 6.3 で TasksPane の `lastView:<projectId>` legacy migration
 * useEffect を撤去し、initializer が hydrate 時に一度だけ吸収する形に
 * 集約した. ここではその migration の挙動を中心にカバーする.
 *
 * Invariants:
 *   IS1  legacy key 不在 → tree は変化しない (idempotent)
 *   IS2  legacy key valid → 全 tasks pane の paneConfig.viewMode に注入
 *   IS3  pane に viewMode 既設定なら legacy 値は上書きしない
 *   IS4  legacy key invalid (任意の文字列) → 無視
 *   IS5  URL `?view=board` + legacy `list` → 最初の tasks pane は board
 *        (URL 勝ち)、残りの tasks pane は list (legacy 注入)
 *   IS6  localStorage が throw する環境 → tree は変化しない (graceful)
 *   IS7  preset (?layout=) で生成された tree も legacy 注入対象
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { initializeWorkbench } from '../../workbench/store/initialState'
import {
  LAYOUT_SCHEMA_VERSION,
  type LayoutTree,
  type PaneType,
} from '../../workbench/types'

const PROJECT = 'proj-IS'
const LAYOUT_KEY = `workbench:layout:${PROJECT}`
const LEGACY_KEY = `lastView:${PROJECT}`

function makeTabsLayout(panes: { paneType: PaneType; viewMode?: string }[]): LayoutTree {
  return {
    id: 'group-1',
    kind: 'tabs',
    activeTabId: 'pane-0',
    tabs: panes.map((p, i) => ({
      id: `pane-${i}`,
      paneType: p.paneType,
      paneConfig: p.viewMode ? { viewMode: p.viewMode } : {},
    })),
  } as LayoutTree
}

function makeSplitLayout(left: LayoutTree, right: LayoutTree): LayoutTree {
  return {
    id: 'split-1',
    kind: 'split',
    orientation: 'horizontal',
    children: [left, right],
    sizes: [50, 50],
  } as LayoutTree
}

function persist(tree: LayoutTree): void {
  window.localStorage.setItem(
    LAYOUT_KEY,
    JSON.stringify({
      version: LAYOUT_SCHEMA_VERSION,
      savedAt: 1,
      tree,
    }),
  )
}

const emptyParams = { get: () => null }
function paramsOf(map: Record<string, string>): { get(name: string): string | null } {
  return { get: (k) => (k in map ? map[k] : null) }
}

beforeEach(() => {
  window.localStorage.clear()
})
afterEach(() => {
  window.localStorage.clear()
  vi.restoreAllMocks()
})

describe('initializeWorkbench — legacy lastView migration (Phase 6.3)', () => {
  it('IS1: legacy key 不在 → tasks pane の viewMode は undefined', () => {
    persist(makeTabsLayout([{ paneType: 'tasks' }]))

    const { state } = initializeWorkbench({
      projectId: PROJECT,
      searchParams: emptyParams,
    })

    if (state.tree.kind !== 'tabs') throw new Error('expected tabs root')
    expect(state.tree.tabs[0].paneConfig).toEqual({})
  })

  it('IS2: legacy `list` → 全 tasks pane に viewMode=list が注入される', () => {
    window.localStorage.setItem(LEGACY_KEY, 'list')
    // 同じ group に tasks pane を 2 つ並べる
    persist(
      makeTabsLayout([
        { paneType: 'tasks' },
        { paneType: 'tasks' },
        { paneType: 'doc' },
      ]),
    )

    const { state } = initializeWorkbench({
      projectId: PROJECT,
      searchParams: emptyParams,
    })

    if (state.tree.kind !== 'tabs') throw new Error('expected tabs root')
    expect(state.tree.tabs[0].paneConfig).toEqual({ viewMode: 'list' })
    expect(state.tree.tabs[1].paneConfig).toEqual({ viewMode: 'list' })
    // doc pane は対象外
    expect(state.tree.tabs[2].paneConfig).toEqual({})
  })

  it('IS3: paneConfig.viewMode が既に設定済なら legacy で上書きしない', () => {
    window.localStorage.setItem(LEGACY_KEY, 'list')
    persist(makeTabsLayout([{ paneType: 'tasks', viewMode: 'timeline' }]))

    const { state } = initializeWorkbench({
      projectId: PROJECT,
      searchParams: emptyParams,
    })

    if (state.tree.kind !== 'tabs') throw new Error('expected tabs root')
    expect(state.tree.tabs[0].paneConfig).toEqual({ viewMode: 'timeline' })
  })

  it('IS4: legacy key の値が ViewMode 定義外 → 無視される', () => {
    window.localStorage.setItem(LEGACY_KEY, 'kanban-mega')
    persist(makeTabsLayout([{ paneType: 'tasks' }]))

    const { state } = initializeWorkbench({
      projectId: PROJECT,
      searchParams: emptyParams,
    })

    if (state.tree.kind !== 'tabs') throw new Error('expected tabs root')
    expect(state.tree.tabs[0].paneConfig).toEqual({})
  })

  it('IS5: URL `?view=board` + legacy `list` → 最初は URL 勝ち、残りは legacy', () => {
    window.localStorage.setItem(LEGACY_KEY, 'list')
    persist(
      makeTabsLayout([
        { paneType: 'tasks' }, // first tasks pane → URL ?view=board
        { paneType: 'tasks' }, // second tasks pane → legacy list
      ]),
    )

    const { state } = initializeWorkbench({
      projectId: PROJECT,
      searchParams: paramsOf({ view: 'board' }),
    })

    if (state.tree.kind !== 'tabs') throw new Error('expected tabs root')
    expect(state.tree.tabs[0].paneConfig).toEqual({ viewMode: 'board' })
    expect(state.tree.tabs[1].paneConfig).toEqual({ viewMode: 'list' })
  })

  it('IS6: localStorage.getItem が throw → tree は変化しない (graceful)', () => {
    persist(makeTabsLayout([{ paneType: 'tasks' }]))
    // load 後、initializer 内の getItem(`lastView:...`) のみ throw させる
    const orig = window.localStorage.getItem.bind(window.localStorage)
    vi.spyOn(window.localStorage, 'getItem').mockImplementation((k) => {
      if (k === LEGACY_KEY) throw new Error('quota')
      return orig(k)
    })

    expect(() =>
      initializeWorkbench({ projectId: PROJECT, searchParams: emptyParams }),
    ).not.toThrow()
    const { state } = initializeWorkbench({
      projectId: PROJECT,
      searchParams: emptyParams,
    })
    if (state.tree.kind !== 'tabs') throw new Error('expected tabs root')
    expect(state.tree.tabs[0].paneConfig).toEqual({})
  })

  it('IS7: split tree (左右に tasks pane が散らばる) でも全て注入される', () => {
    window.localStorage.setItem(LEGACY_KEY, 'timeline')
    const left = makeTabsLayout([{ paneType: 'tasks' }, { paneType: 'doc' }])
    const right = makeTabsLayout([{ paneType: 'tasks' }])
    // split tree を直接 persist (group id 衝突を避けるため右側を書き換え)
    if (right.kind === 'tabs') right.id = 'group-2'
    if (right.kind === 'tabs') right.activeTabId = 'pane-0-r'
    if (right.kind === 'tabs') right.tabs = right.tabs.map((t, i) => ({ ...t, id: `pane-${i}-r` }))
    persist(makeSplitLayout(left, right))

    const { state } = initializeWorkbench({
      projectId: PROJECT,
      searchParams: emptyParams,
    })

    if (state.tree.kind !== 'split') throw new Error('expected split root')
    const [l, r] = state.tree.children
    if (l.kind !== 'tabs' || r.kind !== 'tabs') throw new Error('expected tabs branches')
    expect(l.tabs[0].paneConfig).toEqual({ viewMode: 'timeline' }) // tasks
    expect(l.tabs[1].paneConfig).toEqual({}) // doc → 対象外
    expect(r.tabs[0].paneConfig).toEqual({ viewMode: 'timeline' }) // tasks
  })
})
