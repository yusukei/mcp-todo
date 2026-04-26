/**
 * Editorial Split sidebar — 56 px collapsed rail.
 *
 * Stripped-down counterpart to ``SidebarFull``: the project palette
 * stays accessible (dots only), cross-section icons are kept, and
 * clicking the brand mark expands back to full. Mounted only on
 * ``md`` and above; mobile uses the slideover-style SidebarFull.
 */
import { Link, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen,
  Bookmark,
  ChevronRight,
  Library,
  Shield,
  UserCog,
} from 'lucide-react'
import { projectsApi } from '../../../api/projects'
import { useAuthStore } from '../../../store/auth'
import type { Project } from '../../../types'

interface Props {
  onExpand: () => void
}

export default function SidebarRail({ onExpand }: Props) {
  const location = useLocation()
  const { user } = useAuthStore()
  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list(),
  })

  const activeProjectId =
    location.pathname.match(/^\/projects\/([^/]+)/)?.[1] ?? null
  const isAdminActive = location.pathname.startsWith('/admin')

  return (
    <aside
      className="flex h-full w-14 flex-shrink-0 flex-col items-center border-r border-gray-700/40 bg-gray-950 py-3 text-gray-100"
      aria-label="サイドバー (折りたたみ)"
    >
      {/* Brand mark — clicking expands back to full. */}
      <button
        type="button"
        onClick={onExpand}
        className="mb-3 flex h-8 w-8 items-center justify-center rounded-md hover:bg-gray-700/60"
        title="サイドバーを展開"
        aria-label="サイドバーを展開"
      >
        <span
          aria-hidden
          className="inline-block h-2 w-2 rotate-45 rounded-[2px] bg-accent-500"
        />
      </button>

      {/* Project palette — first 6, dot-only. */}
      <div className="flex flex-1 flex-col items-center gap-1">
        {projects.slice(0, 6).map((p) => {
          const isActive = p.id === activeProjectId
          return (
            <Link
              key={p.id}
              to={`/projects/${p.id}`}
              title={p.name}
              className={[
                'flex h-8 w-8 items-center justify-center rounded-md',
                isActive
                  ? 'border border-gray-700/60 bg-gray-700'
                  : 'hover:bg-gray-700/60',
              ].join(' ')}
            >
              <span
                aria-hidden
                className="h-[9px] w-[9px] rounded-full ring-1 ring-black/20"
                style={{ backgroundColor: p.color ?? undefined }}
              />
            </Link>
          )
        })}

        <div className="my-2 h-px w-6 bg-gray-700/40" aria-hidden />

        {/* Cross-section icons. */}
        <RailIconLink
          to="/bookmarks"
          icon={<Bookmark className="h-3.5 w-3.5" />}
          title="ブックマーク"
          highlighted={location.pathname.startsWith('/bookmarks')}
        />
        <RailIconLink
          to="/knowledge"
          icon={<BookOpen className="h-3.5 w-3.5" />}
          title="ナレッジベース"
          highlighted={location.pathname.startsWith('/knowledge')}
        />
        <RailIconLink
          to="/docsites"
          icon={<Library className="h-3.5 w-3.5" />}
          title="ドキュメントサイト"
          highlighted={location.pathname.startsWith('/docsites')}
        />

        <div className="my-2 h-px w-6 bg-gray-700/40" aria-hidden />

        {user?.is_admin && (
          <RailIconLink
            to="/admin"
            icon={<Shield className="h-3.5 w-3.5" />}
            title="管理者"
            highlighted={isAdminActive}
            accent
          />
        )}
        <RailIconLink
          to="/settings"
          icon={<UserCog className="h-3.5 w-3.5" />}
          title="設定"
          highlighted={location.pathname === '/settings'}
        />
      </div>

      {/* Expand hint + user avatar. */}
      <button
        type="button"
        onClick={onExpand}
        className="mb-2 flex h-7 w-7 items-center justify-center rounded text-gray-300 hover:bg-gray-700/60 hover:text-gray-50"
        title="サイドバーを展開"
        aria-label="サイドバーを展開"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
      <div
        className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-500 text-[11px] font-semibold text-white"
        aria-label={user?.name ?? ''}
      >
        {(user?.name ?? '?').charAt(0).toUpperCase()}
      </div>
    </aside>
  )
}

interface RailIconLinkProps {
  to: string
  icon: React.ReactNode
  title: string
  highlighted: boolean
  /** Pink-tinted variant for the admin entry — matches design spec. */
  accent?: boolean
}

function RailIconLink({
  to,
  icon,
  title,
  highlighted,
  accent = false,
}: RailIconLinkProps) {
  return (
    <Link
      to={to}
      title={title}
      className={[
        'flex h-7 w-7 items-center justify-center rounded-md',
        highlighted
          ? accent
            ? 'border border-gray-700/60 bg-gray-700 text-accent-400'
            : 'border border-gray-700/60 bg-gray-700 text-gray-50'
          : 'text-gray-300 hover:bg-gray-700/60 hover:text-gray-50',
      ].join(' ')}
      aria-label={title}
    >
      {icon}
    </Link>
  )
}
