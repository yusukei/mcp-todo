/**
 * CopyUrlButton (URL S6).
 *
 * 仕様: タスクで指定された Copy URL UI。仕様書 ``docs/api/url-contract.md``
 * の `buildUrl` を介して production-origin の絶対 URL をクリップボードに
 * 書き、SR / hover / focus / touch のいずれでも一貫した挙動を保つ。
 *
 * - `lucide-react` の `Link` / `Check` / `AlertCircle` を状態遷移で出し分け。
 * - `aria-label` は `"Copy URL to {kind}: {title}"` の form。
 * - hover-reveal variant は親要素の `:hover` / `:focus-within` で出る (CSS の
 *   ``group-hover:opacity-100`` を期待。利用側で `<div className="group">` に
 *   くるむ)。touch device (`@media (hover: none)`) では常時 50% 表示で
 *   タップ可能にする。
 * - clipboard 失敗時は `showErrorToast` を表示し、SR には aria-live で通知。
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, Check, Link as LinkIcon } from 'lucide-react'
import { showErrorToast } from './Toast'
import {
  buildUrl,
  type BuildUrlOpts,
  type ResourceKind,
} from '../../lib/urlContract'
import { useCopyToClipboard } from '../../hooks/useCopyToClipboard'

export type CopyUrlButtonVariant = 'hover-reveal' | 'always-visible'
export type CopyUrlButtonSize = 'sm' | 'md'

export interface CopyUrlButtonProps {
  kind: ResourceKind
  /** 24 桁 hex。bookmark / knowledge は project_id 不要、docsite_page は path / siteId を使う。 */
  resourceId?: string
  /** task / document / document_full / project の context。 */
  contextProjectId?: string
  /** docsite_page 用。 */
  siteId?: string
  /** docsite_page 用 (相対 path)。 */
  resourcePath?: string
  /** aria-label に使う表示名 (例: タスクのタイトル)。 */
  title?: string
  variant?: CopyUrlButtonVariant
  size?: CopyUrlButtonSize
  /** 親要素 className 上書き用 (margin / position 調整)。 */
  className?: string
}

const ICON_PX: Record<CopyUrlButtonSize, number> = { sm: 14, md: 16 }
const BUTTON_PX: Record<CopyUrlButtonSize, string> = {
  sm: 'h-6 w-6',
  md: 'h-7 w-7',
}

function variantClass(variant: CopyUrlButtonVariant): string {
  // touch device の検出は CSS の `@media (hover: none)` で素直に表現する。
  // tailwind は標準で hover: prefix を `(hover: hover)` に展開するので、
  // touch では hover: 系が効かない。常時表示の最低 opacity を確保するには
  // 明示的なメディアクエリ class が必要。Tailwind v3 は `pointer-coarse:` を
  // 持たない (custom plugin 不要) ため、内部 style でメディアクエリを
  // 当てるのがシンプル。
  if (variant === 'always-visible') {
    return 'opacity-70 hover:opacity-100 focus-visible:opacity-100'
  }
  // hover-reveal: 親 .group 内で hover/focus 時に出現。touch では sr-only と
  // 等価にすると tap できない → 親に :focus-within もない場合のみ完全に
  // 隠す。touch では下の useTouchDeviceFallback で opacity を上書きする。
  return 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100'
}

function useTouchDeviceFallback(): boolean {
  // SSR / unsupported envs は false (= 非 touch とみなす)。
  const [isTouch, setIsTouch] = useState(false)
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia('(hover: none)')
    const apply = () => setIsTouch(mq.matches)
    apply()
    mq.addEventListener?.('change', apply)
    return () => mq.removeEventListener?.('change', apply)
  }, [])
  return isTouch
}

export default function CopyUrlButton(props: CopyUrlButtonProps) {
  const {
    kind,
    resourceId,
    contextProjectId,
    siteId,
    resourcePath,
    title,
    variant = 'hover-reveal',
    size = 'sm',
    className = '',
  } = props
  const { copy, copied, error } = useCopyToClipboard({ resetMs: 1500 })
  const isTouch = useTouchDeviceFallback()

  const handleClick = useCallback(async () => {
    const opts: BuildUrlOpts = {
      projectId: contextProjectId,
      resourceId,
      siteId,
      path: resourcePath,
      absolute: true,
    }
    let url: string
    try {
      url = buildUrl(kind, opts)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      showErrorToast(`URL を生成できませんでした (${msg})`)
      return
    }
    const ok = await copy(url)
    if (!ok) {
      showErrorToast('URL のコピーに失敗しました')
    }
  }, [copy, kind, contextProjectId, resourceId, siteId, resourcePath])

  const ariaLabel = `Copy URL to ${kind}${title ? `: ${title}` : ''}`

  // 状態に応じたアイコン
  let Icon = LinkIcon
  let iconClass = 'text-gray-300 hover:text-accent-500'
  if (copied) {
    Icon = Check
    iconClass = 'text-status-done'
  } else if (error) {
    Icon = AlertCircle
    iconClass = 'text-pri-urgent'
  }

  // touch device では hover-reveal を 50% opacity で常時表示にする。
  const baseVariant = variantClass(variant)
  const touchOverride =
    isTouch && variant === 'hover-reveal' ? 'opacity-50' : ''

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        aria-label={ariaLabel}
        title={ariaLabel}
        className={`inline-flex items-center justify-center rounded ${BUTTON_PX[size]} transition-opacity ${baseVariant} ${touchOverride} ${className}`.trim()}
      >
        <Icon
          className={iconClass}
          width={ICON_PX[size]}
          height={ICON_PX[size]}
        />
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {copied
          ? 'URL copied to clipboard'
          : error
            ? 'Failed to copy URL'
            : ''}
      </span>
    </>
  )
}
