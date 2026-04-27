/**
 * Editorial Split sidebar — 260 px expanded variant.
 *
 * Owns the entire vertical column for desktop:
 *   - logo + version + collapse button
 *   - 今日の動き (SidebarStat × 4 from ``stats/today``)
 *   - プロジェクト (drag-orderable, with ``task_count`` badges)
 *   - 横断 (ブックマーク / ナレッジ / ドキュメントサイト)
 *   - システム (管理者 / 設定)
 *   - user footer with theme toggle
 *
 * The collapsed variant lives in ``SidebarRail.tsx``. Both share data
 * sources via React Query so toggling collapse never re-fetches.
 */
import { useCallback } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import {
  BookOpen,
  Bookmark,
  ChevronLeft,
  FolderOpen,
  GripVertical,
  Library,
  LogOut,
  Settings,
  Shield,
  UserCog,
  X,
} from 'lucide-react'
import { projectsApi, type ProjectStatsToday } from '../../../api/projects'
import { api } from '../../../api/client'
import { useAuthStore } from '../../../store/auth'
import type { Project } from '../../../types'
import SidebarSection from './SidebarSection'
import SidebarStat from './SidebarStat'
import SidebarLink from './SidebarLink'
import ThemeToggle from '../ThemeToggle'

interface Props {
  /** When mounted as a mobile slideover the close button is shown.
   *  Desktop renders without it because the rail/full toggle handles
   *  collapse instead. */
  onCloseMobile?: () => void
  /** Triggered when the user clicks the «« button. Desktop only. */
  onCollapse?: () => void
}

export default function SidebarFull({ onCloseMobile, onCollapse }: Props) {
  const location = useLocation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { user, logout } = useAuthStore()

  // Active route highlighting — the active project is whichever id
  // appears in the path; for top-level pages we fall back to a section
  // marker (e.g. `'admin'`).
  const activeProjectId =
    location.pathname.match(/^\/projects\/([^/]+)/)?.[1] ?? null
  const activeSection: 'admin' | 'workbench' | null = location.pathname.startsWith(
    '/admin',
  )
    ? 'admin'
    : activeProjectId
    ? 'workbench'
    : null

  // ── Project list (with task_count from API-3) ───────────────
  const { data: projects = [] } = useQuery<Project[]>({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list(),
  })

  // ── stats/today for the active project (API-1) ─────────────
  // Skipping cross-project aggregation in Phase 2 — we focus on the
  // currently-open project so the sidebar stays informative on the
  // Workbench page. When no project is active the section shows dashes.
  const { data: stats } = useQuery<ProjectStatsToday | undefined>({
    queryKey: ['stats:today', activeProjectId],
    queryFn: () =>
      activeProjectId ? projectsApi.statsToday(activeProjectId) : undefined,
    enabled: !!activeProjectId,
    // SSE re-invalidation hook lives in useSSE; treat each fetch as
    // fresh-enough for 30 s so accidental re-mounts don't refire.
    staleTime: 30_000,
  })

  // ── Drag reorder (admin-only) ──────────────────────────────
  const reorderMutation = useMutation({
    mutationFn: (ids: string[]) => api.post('/projects/reorder', { ids }),
    onMutate: async (ids: string[]) => {
      await qc.cancelQueries({ queryKey: ['projects'] })
      const previous = qc.getQueryData<Project[]>(['projects'])
      if (previous) {
        const order = new Map(ids.map((id, i) => [id, i]))
        const sorted = [...previous].sort(
          (a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0),
        )
        qc.setQueryData(['projects'], sorted)
      }
      return { previous }
    },
    onError: (_err, _ids, context) => {
      if (context?.previous) qc.setQueryData(['projects'], context.previous)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event
      if (!over || active.id === over.id) return
      const ids = projects.map((p) => p.id)
      const oldIndex = ids.indexOf(active.id as string)
      const newIndex = ids.indexOf(over.id as string)
      if (oldIndex < 0 || newIndex < 0) return
      reorderMutation.mutate(arrayMove(ids, oldIndex, newIndex))
    },
    [projects, reorderMutation],
  )

  const closeMobile = onCloseMobile ?? (() => {})

  return (
    <aside className="flex h-full w-[260px] flex-shrink-0 flex-col border-r border-line-2 bg-gray-950 text-gray-100">
      {/* ── Header: brand + collapse / mobile close ─────────── */}
      <div className="flex items-start gap-2 border-b border-line-2 px-5 py-5">
        <div className="flex-1">
          <Link
            to="/projects"
            className="flex items-center gap-2 text-gray-50"
            onClick={closeMobile}
          >
            <span
              aria-hidden
              className="inline-block h-2 w-2 rotate-45 rounded-[2px] bg-accent-500"
            />
            <span className="font-serif text-[18px] font-bold tracking-[-0.01em]">
              MCP Todo
            </span>
          </Link>
          <div className="mt-1 font-mono text-[10.5px] text-gray-300">
            {/* P2-D: ISO 文字列 (2026-04-27T01:33:21.143Z) は折り返しで
                サイドバーが膨らむ。MM-DD HH:MM の短縮形に整形して
                設計プロトの 'build 04-26T11:04' 表現に揃える。 */}
            build {formatBuildStamp(__BUILD_TIMESTAMP__)}
          </div>
        </div>
        {onCloseMobile && (
          <button
            type="button"
            onClick={onCloseMobile}
            className="rounded p-1 text-gray-300 hover:bg-gray-700/60 hover:text-gray-50 md:hidden"
            aria-label="サイドバーを閉じる"
          >
            <X className="h-5 w-5" />
          </button>
        )}
        {onCollapse && (
          <button
            type="button"
            onClick={onCollapse}
            className="hidden h-7 w-7 items-center justify-center rounded text-gray-300 hover:bg-gray-700/60 hover:text-gray-50 md:flex"
            aria-label="サイドバーを折りたたむ"
            title="サイドバーを折りたたむ (rail)"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* ── Scrollable middle ───────────────────────────────── */}
      <nav className="scroll flex-1 overflow-y-auto py-3">
        {/* 今日の動き — only meaningful when a project is active. */}
        <SidebarSection title="今日の動き">
          <SidebarStat
            label="進行中"
            value={stats?.in_progress}
            tintClass="bg-status-progress"
          />
          <SidebarStat
            label="許可待ち"
            value={stats?.awaiting_decision}
            tintClass="bg-blocked"
          />
          <SidebarStat
            label="完了 (24h)"
            value={stats?.completed_24h}
            tintClass="bg-approved"
          />
        </SidebarSection>

        {/* プロジェクト */}
        <SidebarSection title="プロジェクト">
          <Link
            to="/projects"
            onClick={closeMobile}
            className={[
              'mx-2 flex items-center gap-2.5 rounded-md px-3 py-1.5 text-[13px]',
              activeSection === null && location.pathname === '/projects'
                ? 'bg-gray-700 font-medium text-gray-50'
                : 'text-gray-100 hover:bg-gray-700/60',
            ].join(' ')}
          >
            <FolderOpen className="h-4 w-4 text-gray-300" />
            <span className="flex-1 truncate">すべて</span>
          </Link>
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={projects.map((p) => p.id)}
              strategy={verticalListSortingStrategy}
            >
              {projects.map((p) => (
                <SortableProjectRow
                  key={p.id}
                  project={p}
                  isActive={p.id === activeProjectId}
                  isAdmin={!!user?.is_admin}
                  closeSidebar={closeMobile}
                />
              ))}
            </SortableContext>
          </DndContext>
        </SidebarSection>

        {/* 横断 */}
        <SidebarSection title="横断">
          <SidebarLink
            to="/bookmarks"
            icon={<Bookmark className="h-3.5 w-3.5" />}
            label="ブックマーク"
            highlighted={location.pathname.startsWith('/bookmarks')}
            onClick={closeMobile}
          />
          <SidebarLink
            to="/knowledge"
            icon={<BookOpen className="h-3.5 w-3.5" />}
            label="ナレッジベース"
            highlighted={location.pathname.startsWith('/knowledge')}
            onClick={closeMobile}
          />
          <SidebarLink
            to="/docsites"
            icon={<Library className="h-3.5 w-3.5" />}
            label="ドキュメントサイト"
            highlighted={location.pathname.startsWith('/docsites')}
            onClick={closeMobile}
          />
        </SidebarSection>

        {/* システム — admin gate */}
        <SidebarSection title="システム">
          {user?.is_admin && (
            <SidebarLink
              to="/admin"
              icon={<Shield className="h-3.5 w-3.5" />}
              label="管理者"
              highlighted={activeSection === 'admin'}
              onClick={closeMobile}
            />
          )}
          <SidebarLink
            to="/settings"
            icon={<UserCog className="h-3.5 w-3.5" />}
            label="設定"
            highlighted={location.pathname === '/settings'}
            onClick={closeMobile}
          />
        </SidebarSection>
      </nav>

      {/* ── User footer ─────────────────────────────────────── */}
      <div className="flex items-center gap-2 border-t border-line-2 px-3 py-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-500 text-[11px] font-semibold text-white">
          {(user?.name ?? '?').charAt(0).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-medium text-gray-50">
            {user?.name ?? '...'}
          </div>
          <div className="truncate text-[10px] text-gray-300">
            {user?.is_admin ? 'admin' : 'member'}
          </div>
        </div>
        <ThemeToggle />
        <button
          type="button"
          onClick={() => {
            logout()
            navigate('/login')
          }}
          className="rounded p-1 text-gray-300 hover:bg-gray-700/60 hover:text-gray-50"
          title="ログアウト"
          aria-label="ログアウト"
        >
          <LogOut className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* P0-1: 「AI が n 件作業中」は Workbench メイン領域の右下 FAB
          (WorkbenchPage の <ActiveAiPill/>) に移設したのでここでは
          表示しない。設計プロト variant-b.jsx の position:absolute;
          bottom:18; right:22 と整合。 */}
    </aside>
  )
}

// ── SortableProjectRow (admin can drag, others read-only) ────────

interface RowProps {
  project: Project
  isActive: boolean
  isAdmin: boolean
  closeSidebar: () => void
}

function SortableProjectRow({
  project,
  isActive,
  isAdmin,
  closeSidebar,
}: RowProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: project.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={[
        'group/project mx-2 flex items-center rounded-md',
        isActive ? 'bg-gray-700 text-gray-50' : 'hover:bg-gray-700/60',
      ].join(' ')}
    >
      {isAdmin && (
        <button
          {...attributes}
          {...listeners}
          className="ml-1 flex-shrink-0 cursor-grab p-0.5 text-gray-300/40 hover:text-gray-300 active:cursor-grabbing"
          tabIndex={-1}
          aria-label="並べ替え"
        >
          <GripVertical className="h-3 w-3" />
        </button>
      )}
      <Link
        to={`/projects/${project.id}`}
        onClick={closeSidebar}
        className={[
          'flex min-w-0 flex-1 items-center gap-2 px-2.5 py-1.5 text-[13px]',
          isActive ? 'font-medium text-gray-50' : 'text-gray-100',
        ].join(' ')}
      >
        <span
          aria-hidden
          className="h-2 w-2 flex-shrink-0 rounded-full ring-1 ring-black/20"
          style={{ backgroundColor: project.color ?? undefined }}
        />
        <span className="flex-1 truncate">{project.name}</span>
        {project.task_count !== undefined && project.task_count > 0 && (
          <span className="font-mono text-[10px] text-gray-300">
            {project.task_count}
          </span>
        )}
      </Link>
      <Link
        to={`/projects/${project.id}/settings`}
        onClick={closeSidebar}
        className="mr-1 flex-shrink-0 rounded p-1 text-gray-300/40 opacity-0 transition-opacity hover:text-gray-200 group-hover/project:opacity-100"
        title="プロジェクト設定"
        aria-label={`${project.name} 設定`}
      >
        <Settings className="h-3 w-3" />
      </Link>
    </div>
  )
}

// P2-D: ISO build timestamp を `MM-DD HH:MM` の短縮形に。失敗時は
// 元文字列を先頭 16 文字 (= ISO の `YYYY-MM-DDTHH:MM`) で打ち切る。
function formatBuildStamp(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}
