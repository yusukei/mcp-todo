/**
 * AdminHeader (P1-B) — admin / settings 系画面の上部ヘッダ。
 *
 * 設計プロト variant-admin.jsx の AdminShell ヘッダ:
 *   - overline: `ADMIN` (uppercase tracking 0.14em font-mono 11px ink-4)
 *   - title:    Fraunces 28px font-bold tracking -0.02em ink-1
 *   - subtitle: 13px ink-3 (任意)
 *   - actions:  右側の補助ボタン群 (任意)
 *
 * 配色は dark 専用 (bg-gray-900 / line-2 border)。BACK 戻るリンク
 * は backTo を渡されたときだけ表示する (UserDetailPage 等で使用)。
 */
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'

interface Props {
  /** uppercase で表示する小さなセクションラベル (例: "ADMIN", "USER")。 */
  overline?: string
  /** Fraunces で組まれるメインタイトル。 */
  title: string
  /** title の下に小さく出る補足。 */
  subtitle?: string
  /** 右側に並べる action 要素 (button / link)。 */
  actions?: React.ReactNode
  /** 戻るリンク先 path。指定時はタイトル左に back ボタンを表示。 */
  backTo?: string
  /** 戻るボタンのラベル。デフォルトは「戻る」。 */
  backLabel?: string
}

export default function AdminHeader({
  overline,
  title,
  subtitle,
  actions,
  backTo,
  backLabel = '戻る',
}: Props) {
  return (
    <header className="border-b border-line-2 bg-gray-900 px-8 py-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          {backTo && (
            <Link
              to={backTo}
              className="mb-2 inline-flex items-center gap-1 text-[11px] text-gray-300 hover:text-accent-400 transition-colors"
            >
              <ArrowLeft className="h-3 w-3" />
              {backLabel}
            </Link>
          )}
          {overline && (
            <div className="mb-1 font-mono text-[11px] uppercase tracking-[0.14em] text-gray-300">
              {overline}
            </div>
          )}
          <h1 className="font-serif text-[28px] font-bold leading-tight text-gray-50 tracking-[-0.02em]">
            {title}
          </h1>
          {subtitle && (
            <p className="mt-1 text-[13px] text-gray-200">{subtitle}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2 flex-shrink-0">{actions}</div>}
      </div>
    </header>
  )
}
