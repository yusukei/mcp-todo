/**
 * Clipboard write hook (URL S6).
 *
 * Modern path: ``navigator.clipboard.writeText``。HTTPS / localhost で
 * 動作。失敗時 (insecure context, browser block, permissions) は
 * ``execCommand('copy')`` の textarea trick に fallback する。
 *
 * 状態:
 *   - ``copied``: 直近 ``resetMs`` ms 以内にコピー成功
 *   - ``error``: 直近の失敗 (Error or null)
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export interface UseCopyToClipboardOpts {
  /** copied state を自動 reset するまでの ms。デフォルト 1500。 */
  resetMs?: number
}

export interface UseCopyToClipboardResult {
  copy: (text: string) => Promise<boolean>
  copied: boolean
  error: Error | null
  reset: () => void
}

const DEFAULT_RESET_MS = 1500

async function copyViaClipboardApi(text: string): Promise<void> {
  if (
    typeof navigator === 'undefined' ||
    !navigator.clipboard ||
    typeof navigator.clipboard.writeText !== 'function'
  ) {
    throw new Error('Clipboard API unavailable')
  }
  await navigator.clipboard.writeText(text)
}

function copyViaExecCommand(text: string): void {
  if (typeof document === 'undefined') {
    throw new Error('document unavailable')
  }
  // 一時 textarea。selection は確実に書き換えるため style で隠す。
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.top = '0'
  textarea.style.left = '0'
  textarea.style.width = '1px'
  textarea.style.height = '1px'
  textarea.style.padding = '0'
  textarea.style.border = 'none'
  textarea.style.outline = 'none'
  textarea.style.boxShadow = 'none'
  textarea.style.background = 'transparent'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  try {
    textarea.focus()
    textarea.select()
    const ok = document.execCommand('copy')
    if (!ok) throw new Error('execCommand("copy") returned false')
  } finally {
    document.body.removeChild(textarea)
  }
}

export function useCopyToClipboard(
  opts: UseCopyToClipboardOpts = {},
): UseCopyToClipboardResult {
  const resetMs = opts.resetMs ?? DEFAULT_RESET_MS
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const reset = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setCopied(false)
    setError(null)
  }, [])

  // unmount で pending timer を片付ける.
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }
  }, [])

  const copy = useCallback(
    async (text: string): Promise<boolean> => {
      // 直近 timer を捨てて状態を最新クリックに合わせる.
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
      setError(null)
      try {
        await copyViaClipboardApi(text)
      } catch (modernErr) {
        try {
          copyViaExecCommand(text)
        } catch (legacyErr) {
          const err =
            legacyErr instanceof Error
              ? legacyErr
              : new Error(String(legacyErr))
          setCopied(false)
          setError(err)
          return false
        }
        // fallback path 成功でもユーザに見える挙動は同じ (冗長ログなし).
        void modernErr
      }
      setCopied(true)
      timerRef.current = setTimeout(() => {
        setCopied(false)
        timerRef.current = null
      }, resetMs)
      return true
    },
    [resetMs],
  )

  return { copy, copied, error, reset }
}
