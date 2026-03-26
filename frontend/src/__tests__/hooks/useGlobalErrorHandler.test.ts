import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useGlobalErrorHandler } from '../../hooks/useGlobalErrorHandler'
import * as Toast from '../../components/common/Toast'

vi.mock('../../components/common/Toast', () => ({
  showErrorToast: vi.fn(),
}))

describe('useGlobalErrorHandler', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('unhandledrejection で Error を受け取った場合 toast を表示する', () => {
    renderHook(() => useGlobalErrorHandler())

    const event = new Event('unhandledrejection') as PromiseRejectionEvent
    Object.defineProperty(event, 'reason', { value: new Error('async failure') })
    window.dispatchEvent(event)

    expect(Toast.showErrorToast).toHaveBeenCalledWith('async failure')
  })

  it('unhandledrejection で文字列を受け取った場合 toast を表示する', () => {
    renderHook(() => useGlobalErrorHandler())

    const event = new Event('unhandledrejection') as PromiseRejectionEvent
    Object.defineProperty(event, 'reason', { value: 'string error' })
    window.dispatchEvent(event)

    expect(Toast.showErrorToast).toHaveBeenCalledWith('string error')
  })

  it('unhandledrejection で不明な reason の場合デフォルトメッセージを表示する', () => {
    renderHook(() => useGlobalErrorHandler())

    const event = new Event('unhandledrejection') as PromiseRejectionEvent
    Object.defineProperty(event, 'reason', { value: 42 })
    window.dispatchEvent(event)

    expect(Toast.showErrorToast).toHaveBeenCalledWith('予期しないエラーが発生しました')
  })

  it('401 レスポンスのエラーは無視する', () => {
    renderHook(() => useGlobalErrorHandler())

    const error = new Error('Unauthorized')
    ;(error as any).response = { status: 401 }
    const event = new Event('unhandledrejection') as PromiseRejectionEvent
    Object.defineProperty(event, 'reason', { value: error })
    window.dispatchEvent(event)

    expect(Toast.showErrorToast).not.toHaveBeenCalled()
  })

  it('403 レスポンスのエラーは無視する', () => {
    renderHook(() => useGlobalErrorHandler())

    const error = new Error('Forbidden')
    ;(error as any).response = { status: 403 }
    const event = new Event('unhandledrejection') as PromiseRejectionEvent
    Object.defineProperty(event, 'reason', { value: error })
    window.dispatchEvent(event)

    expect(Toast.showErrorToast).not.toHaveBeenCalled()
  })

  it('error イベントで toast を表示する', () => {
    renderHook(() => useGlobalErrorHandler())

    const event = new ErrorEvent('error', { message: 'runtime error', error: new Error('runtime error') })
    window.dispatchEvent(event)

    expect(Toast.showErrorToast).toHaveBeenCalledWith('runtime error')
  })

  it('unmount 時にリスナーを解除する', () => {
    const { unmount } = renderHook(() => useGlobalErrorHandler())
    unmount()

    const event = new Event('unhandledrejection') as PromiseRejectionEvent
    Object.defineProperty(event, 'reason', { value: new Error('should not show') })
    window.dispatchEvent(event)

    expect(Toast.showErrorToast).not.toHaveBeenCalled()
  })
})
