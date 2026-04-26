/**
 * Single row inside the "今日の動き" section: a colored tint bar, a
 * label, and a Fraunces-styled value. Drives off the API-1 response
 * (`ProjectStatsToday`).
 */
import { useId } from 'react'

interface Props {
  label: string
  value: number | undefined
  /** Tailwind background utility (e.g. ``bg-status-progress``) used
   *  for the 4×14 tint bar — keeps colour configurable from the call
   *  site without leaking design tokens here. */
  tintClass: string
}

export default function SidebarStat({ label, value, tintClass }: Props) {
  const id = useId()
  // Show a dash while loading rather than blanking the layout. We
  // intentionally do not show 0 vs unknown distinctly — the parent
  // SidebarFull only mounts once we have a project id.
  const display = value === undefined ? '–' : String(value)
  return (
    <div className="mx-2 mb-0.5 flex items-center gap-3 px-3 py-1.5 text-xs text-gray-100">
      <span
        aria-hidden
        className={`inline-block h-3.5 w-1 flex-shrink-0 rounded-sm ${tintClass}`}
      />
      <span id={id} className="flex-1 truncate">
        {label}
      </span>
      <span
        aria-labelledby={id}
        className="font-serif text-[15px] font-semibold text-gray-50 tabular-nums"
      >
        {display}
      </span>
    </div>
  )
}
