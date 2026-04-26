/**
 * URL contract tests (INV-1 / INV-7 partial coverage). The full
 * round-trip / state-sync tests live in higher-level integration
 * tests (D3 manual checklist + future fixture). Here we validate
 * the pure parser/serializer + tree helpers — the foundation other
 * tests build on.
 */
import { describe, expect, it } from 'vitest'
import {
  findFirstPaneOfType,
  findFirstTabsNodeId,
  findTabsNodeContaining,
  parseUrlContract,
  searchParamsEqual,
  serialiseUrlContract,
} from '../../workbench/urlContract'
import {
  makePane,
  makeSplitNode,
  makeTabsNode,
} from '../../workbench/treeUtils'

describe('parseUrlContract', () => {
  const params = (s: string) => new URLSearchParams(s)

  it('parses task / doc / view / layout / group', () => {
    const c = parseUrlContract(
      params('task=t1&doc=d1&view=board&layout=tasks-only&group=assignee'),
    )
    expect(c.task).toBe('t1')
    expect(c.doc).toBe('d1')
    expect(c.view).toBe('board')
    expect(c.layout).toBe('tasks-only')
    expect(c.group).toBe('assignee')
    expect(c.legacyViewToAdd).toBeNull()
    expect(c.hadUnknownValue).toBe(false)
  })

  it('returns nulls for missing params', () => {
    const c = parseUrlContract(params(''))
    expect(c.task).toBeNull()
    expect(c.doc).toBeNull()
    expect(c.view).toBeNull()
    expect(c.layout).toBeNull()
    expect(c.group).toBeNull()
    expect(c.legacyViewToAdd).toBeNull()
    expect(c.hadUnknownValue).toBe(false)
  })

  it('treats whitespace-only values as null', () => {
    const c = parseUrlContract(params('task=&doc=%20%20'))
    expect(c.task).toBeNull()
    expect(c.doc).toBeNull()
  })

  it('maps legacy ?view=docs to the documents pane', () => {
    const c = parseUrlContract(params('view=docs'))
    expect(c.view).toBeNull()
    expect(c.legacyViewToAdd).toBe('documents')
  })

  it.each([
    ['files', 'file-browser'],
    ['errors', 'error-tracker'],
  ] as const)(
    'maps legacy ?view=%s → %s pane',
    (legacy, expected) => {
      const c = parseUrlContract(params(`view=${legacy}`))
      expect(c.legacyViewToAdd).toBe(expected)
    },
  )

  it('falls back + flags unknown when ?view= is gibberish', () => {
    const c = parseUrlContract(params('view=fr0g'))
    expect(c.view).toBeNull()
    expect(c.legacyViewToAdd).toBeNull()
    expect(c.hadUnknownValue).toBe(true)
  })
})

describe('serialiseUrlContract', () => {
  it('sets / removes / preserves keys per patch', () => {
    const initial = new URLSearchParams('task=A&existing=keepme')
    const next = serialiseUrlContract(initial, {
      task: 'B', // overwrite
      doc: 'D', // add
      view: null, // no-op (key wasn't there)
    })
    expect(next.get('task')).toBe('B')
    expect(next.get('doc')).toBe('D')
    expect(next.get('view')).toBeNull()
    expect(next.get('existing')).toBe('keepme')
  })

  it('null / empty string deletes the key', () => {
    const initial = new URLSearchParams('task=A&doc=B')
    const next = serialiseUrlContract(initial, { task: null, doc: '' })
    expect(next.get('task')).toBeNull()
    expect(next.get('doc')).toBeNull()
  })

  it('undefined skips the key entirely', () => {
    const initial = new URLSearchParams('task=A')
    const next = serialiseUrlContract(initial, { task: undefined })
    expect(next.get('task')).toBe('A')
  })
})

describe('searchParamsEqual', () => {
  it('returns true for permuted equal entries', () => {
    expect(
      searchParamsEqual(
        new URLSearchParams('a=1&b=2'),
        new URLSearchParams('b=2&a=1'),
      ),
    ).toBe(true)
  })
  it('returns false on different lengths', () => {
    expect(
      searchParamsEqual(
        new URLSearchParams('a=1'),
        new URLSearchParams('a=1&b=2'),
      ),
    ).toBe(false)
  })
  it('returns false on different values', () => {
    expect(
      searchParamsEqual(
        new URLSearchParams('a=1'),
        new URLSearchParams('a=2'),
      ),
    ).toBe(false)
  })
})

describe('findFirstPaneOfType / findFirstTabsNodeId / findTabsNodeContaining', () => {
  it('finds first pane of type in a single tab group', () => {
    const a = makePane('tasks')
    const b = makePane('doc')
    const tree = makeTabsNode([a, b])
    expect(findFirstPaneOfType(tree, 'doc')?.id).toBe(b.id)
    expect(findFirstPaneOfType(tree, 'terminal')).toBeNull()
  })

  it('walks splits depth-first to find first pane', () => {
    const a = makePane('tasks')
    const b = makePane('doc')
    const c = makePane('doc')
    const tree = makeSplitNode('horizontal', [
      makeTabsNode([a]),
      makeTabsNode([b, c]),
    ])
    // First doc in DFS order = b (in the right subtree, but DFS
    // descends left-first: left subtree has only `a`, right has b
    // first then c).
    expect(findFirstPaneOfType(tree, 'doc')?.id).toBe(b.id)
  })

  it('findFirstTabsNodeId returns the leftmost tab group id', () => {
    const left = makeTabsNode([makePane('tasks')])
    const right = makeTabsNode([makePane('doc')])
    const tree = makeSplitNode('horizontal', [left, right])
    expect(findFirstTabsNodeId(tree)).toBe(left.id)
  })

  it('findTabsNodeContaining returns the group that owns a pane', () => {
    const a = makePane('tasks')
    const b = makePane('doc')
    const groupA = makeTabsNode([a])
    const groupB = makeTabsNode([b])
    const tree = makeSplitNode('horizontal', [groupA, groupB])
    expect(findTabsNodeContaining(tree, b.id)?.id).toBe(groupB.id)
    expect(findTabsNodeContaining(tree, 'nope')).toBeNull()
  })
})
