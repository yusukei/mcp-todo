/**
 * Section header used inside SidebarFull.
 *
 * Mirrors the design-spec convention (`UI 再設計仕様書 §3.1`): small
 * uppercase Fraunces label with wide tracking, sitting above a column
 * of SidebarLink / SidebarStat / project rows.
 */
import type { ReactNode } from 'react'

interface Props {
  title: string
  children: ReactNode
}

export default function SidebarSection({ title, children }: Props) {
  return (
    <div className="mb-4">
      <div className="font-serif px-5 pb-2 text-[11px] uppercase tracking-[0.14em] font-semibold text-gray-300">
        {title}
      </div>
      {children}
    </div>
  )
}
