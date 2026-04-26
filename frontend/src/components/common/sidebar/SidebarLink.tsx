/**
 * A row in the "横断" / "システム" sidebar sections — icon + label +
 * optional badge. Wraps react-router's Link so callers can pass any
 * relative path.
 */
import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

interface Props {
  to: string
  icon: ReactNode
  label: string
  /** Optional accent — e.g. the active "管理者" entry should pop in
   *  pink. Passed as an extra Tailwind class on the icon span. */
  highlighted?: boolean
  /** Trailing badge (e.g. open task count). Pass a small string. */
  badge?: string
  onClick?: () => void
}

export default function SidebarLink({
  to,
  icon,
  label,
  highlighted = false,
  badge,
  onClick,
}: Props) {
  return (
    <Link
      to={to}
      onClick={onClick}
      className={[
        'mx-2 flex items-center gap-2.5 rounded-md px-3 py-1.5 text-[13px]',
        highlighted
          ? 'bg-gray-700 font-medium text-gray-50'
          : 'text-gray-100 hover:bg-gray-700/60',
      ].join(' ')}
    >
      <span
        className={[
          'inline-flex h-4 w-4 items-center justify-center',
          highlighted ? 'text-accent-400' : 'text-gray-300',
        ].join(' ')}
        aria-hidden
      >
        {icon}
      </span>
      <span className="flex-1 truncate">{label}</span>
      {badge && (
        <span className="font-mono text-[10px] text-gray-300">{badge}</span>
      )}
    </Link>
  )
}
