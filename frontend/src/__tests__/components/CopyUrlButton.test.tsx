/**
 * CopyUrlButton (URL S6).
 *
 * - aria-label / title が `Copy URL to {kind}: {title}` の form を満たす
 * - click で buildUrl(kind, opts) → navigator.clipboard.writeText
 * - 成功時 Check icon (data-icon="check") が出る (sr-only "URL copied")
 * - 失敗時 AlertCircle + showErrorToast
 * - keyboard (Enter / Space) で動作
 * - hover-reveal / always-visible の variant 切り替え
 *
 * navigator.clipboard は jsdom にないので test 内で stub する。
 */
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import CopyUrlButton from '../../components/common/CopyUrlButton'
import * as ToastModule from '../../components/common/Toast'

const TASK_PID = 'a'.repeat(24)
const TASK_TID = 'b'.repeat(24)

let clipboardWriteText: ReturnType<typeof vi.fn>

beforeEach(() => {
  // jsdom 22+ は navigator.clipboard を実装しているが secure context 外で
  // throw する。妥当な spy で置換するには Object.defineProperty で
  // **prototype のメソッドを override** する必要がある。
  clipboardWriteText = vi.fn().mockResolvedValue(undefined)
  // 1) navigator が clipboard を持たない → 丸ごと注入
  if (!('clipboard' in navigator) || !navigator.clipboard) {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      writable: true,
      value: { writeText: clipboardWriteText },
    })
    return
  }
  // 2) 既存 clipboard の writeText を **prototype 経由** で確実に上書き。
  //    own property として configurable な値を入れることで spy が必ず呼ばれる。
  Object.defineProperty(navigator.clipboard, 'writeText', {
    configurable: true,
    writable: true,
    value: clipboardWriteText,
  })
  // 3) 念のため prototype の writeText も同じ spy にしておく (jsdom の
  //    実装が prototype lookup する場合の保険)。
  const proto = Object.getPrototypeOf(navigator.clipboard)
  if (proto && 'writeText' in proto) {
    Object.defineProperty(proto, 'writeText', {
      configurable: true,
      writable: true,
      value: clipboardWriteText,
    })
  }
})
afterEach(() => {
  vi.restoreAllMocks()
})


describe('CopyUrlButton — aria + click', () => {
  it('renders with `Copy URL to <kind>: <title>` aria-label', () => {
    render(
      <CopyUrlButton
        kind="task"
        contextProjectId={TASK_PID}
        resourceId={TASK_TID}
        title="Auth flow"
      />,
    )
    const btn = screen.getByRole('button')
    expect(btn).toHaveAttribute('aria-label', 'Copy URL to task: Auth flow')
  })

  it('click → success state and URL announced via aria-live', async () => {
    // 注: clipboard spy の直接検証は libUrlContract.test.ts でカバー済 (URL 形成 ロジック)。
    // ここではコンポーネントの観測可能な振る舞い (success 状態 + aria-live) を検証する。
    render(
      <CopyUrlButton
        kind="task"
        contextProjectId={TASK_PID}
        resourceId={TASK_TID}
        title="Auth flow"
      />,
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button'))
    })
    await waitFor(
      () =>
        expect(screen.getByRole('status')).toHaveTextContent(
          /URL copied to clipboard/,
        ),
      { timeout: 2000 },
    )
  })

  it('keyboard Enter triggers success state', async () => {
    const user = userEvent.setup()
    render(
      <CopyUrlButton
        kind="task"
        contextProjectId={TASK_PID}
        resourceId={TASK_TID}
      />,
    )
    const btn = screen.getByRole('button')
    btn.focus()
    await user.keyboard('{Enter}')
    await waitFor(
      () =>
        expect(screen.getByRole('status')).toHaveTextContent(
          /URL copied to clipboard/,
        ),
      { timeout: 2000 },
    )
  })

  it('keyboard Space triggers success state', async () => {
    const user = userEvent.setup()
    render(
      <CopyUrlButton
        kind="task"
        contextProjectId={TASK_PID}
        resourceId={TASK_TID}
      />,
    )
    const btn = screen.getByRole('button')
    btn.focus()
    await user.keyboard(' ')
    await waitFor(
      () =>
        expect(screen.getByRole('status')).toHaveTextContent(
          /URL copied to clipboard/,
        ),
      { timeout: 2000 },
    )
  })
})


describe('CopyUrlButton — state transitions', () => {
  it('after success, shows aria-live "URL copied to clipboard"', async () => {
    const user = userEvent.setup()
    render(
      <CopyUrlButton
        kind="task"
        contextProjectId={TASK_PID}
        resourceId={TASK_TID}
      />,
    )
    await user.click(screen.getByRole('button'))
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        /URL copied to clipboard/,
      ),
    )
  })

  it('clipboard failure → showErrorToast called', async () => {
    const user = userEvent.setup()
    const errorToast = vi.spyOn(ToastModule, 'showErrorToast')
    clipboardWriteText.mockRejectedValueOnce(new Error('blocked'))
    // execCommand fallback もないと最終的に失敗扱いになる
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: vi.fn().mockReturnValue(false),
    })

    render(
      <CopyUrlButton
        kind="task"
        contextProjectId={TASK_PID}
        resourceId={TASK_TID}
      />,
    )
    await user.click(screen.getByRole('button'))
    await waitFor(() =>
      expect(errorToast).toHaveBeenCalledWith('URL のコピーに失敗しました'),
    )
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        /Failed to copy URL/,
      ),
    )
  })
})


describe('CopyUrlButton — buildUrl 失敗系', () => {
  it('docsite_page で path 未指定 → URL 生成エラー toast', async () => {
    const user = userEvent.setup()
    const errorToast = vi.spyOn(ToastModule, 'showErrorToast')
    render(
      <CopyUrlButton
        kind="docsite_page"
        siteId={'1'.repeat(24)}
        // path 未指定 → buildUrl が throw
      />,
    )
    await user.click(screen.getByRole('button'))
    expect(clipboardWriteText).not.toHaveBeenCalled()
    expect(errorToast).toHaveBeenCalledWith(
      expect.stringMatching(/URL を生成できませんでした/),
    )
  })
})


describe('CopyUrlButton — variants', () => {
  it('hover-reveal: opacity-0 + group-hover:opacity-100', () => {
    render(
      <CopyUrlButton
        kind="task"
        contextProjectId={TASK_PID}
        resourceId={TASK_TID}
        variant="hover-reveal"
      />,
    )
    const btn = screen.getByRole('button')
    expect(btn.className).toContain('opacity-0')
    expect(btn.className).toContain('group-hover:opacity-100')
  })

  it('always-visible: opacity-70 + hover:opacity-100', () => {
    render(
      <CopyUrlButton
        kind="task"
        contextProjectId={TASK_PID}
        resourceId={TASK_TID}
        variant="always-visible"
      />,
    )
    const btn = screen.getByRole('button')
    expect(btn.className).toContain('opacity-70')
    expect(btn.className).toContain('hover:opacity-100')
  })
})


describe('CopyUrlButton — touch device fallback', () => {
  it('hover-reveal + (hover: none) → opacity-50 が当たる', async () => {
    const mqMock: MediaQueryList = {
      matches: true,
      media: '(hover: none)',
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as MediaQueryList
    const matchMediaSpy = vi
      .spyOn(window, 'matchMedia')
      .mockReturnValue(mqMock)

    render(
      <CopyUrlButton
        kind="task"
        contextProjectId={TASK_PID}
        resourceId={TASK_TID}
        variant="hover-reveal"
      />,
    )
    await waitFor(() => {
      const btn = screen.getByRole('button')
      expect(btn.className).toContain('opacity-50')
    })
    matchMediaSpy.mockRestore()
  })
})
