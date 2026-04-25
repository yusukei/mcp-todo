import { useState, useCallback } from 'react'
import { Link, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  DndContext, closestCenter, PointerSensor, useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  SortableContext, verticalListSortingStrategy, useSortable, arrayMove,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { FolderOpen, LogOut, Settings, UserCog, CheckSquare, Menu, X, BookOpen, Bookmark, Library, TerminalSquare, GripVertical } from 'lucide-react'
import { api } from '../../api/client'
import { useAuthStore } from '../../store/auth'
import { useSSE } from '../../hooks/useSSE'
import LiveActivityPanel from './LiveActivityPanel'
import ThemeToggle from './ThemeToggle'
import ErrorBoundary, { PageErrorFallback } from './ErrorBoundary'
import type { Project } from '../../types'

// ── Sortable project item ──────────────────────────
function SortableProjectItem({ project, closeSidebar, isAdmin, isActive }: {
  project: Project
  closeSidebar: () => void
  isAdmin: boolean
  isActive: boolean
}) {
  const {
    attributes, listeners, setNodeRef, transform, transition, isDragging,
  } = useSortable({ id: project.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div ref={setNodeRef} style={style} className={`group/project flex items-center rounded-lg ${isActive ? 'bg-terracotta-50 dark:bg-terracotta-900/30' : 'hover:bg-gray-100 dark:hover:bg-gray-700'}`}>
      {isAdmin && (
        <button
          {...attributes}
          {...listeners}
          className="flex-shrink-0 p-1 ml-1 cursor-grab active:cursor-grabbing text-gray-300 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400"
          tabIndex={-1}
        >
          <GripVertical className="w-3.5 h-3.5" />
        </button>
      )}
      <Link
        to={`/projects/${project.id}`}
        onClick={closeSidebar}
        className={`flex-1 flex items-center gap-2 px-2 py-2 text-sm min-w-0 ${isActive ? 'text-terracotta-700 dark:text-terracotta-300 font-medium' : 'text-gray-600 dark:text-gray-300'}`}
      >
        <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: project.color ?? undefined }} />
        <span className="truncate">{project.name}</span>
      </Link>
      <Link
        to={`/projects/${project.id}/settings`}
        onClick={closeSidebar}
        className="flex-shrink-0 p-1.5 mr-1 rounded text-gray-300 dark:text-gray-600 opacity-0 group-hover/project:opacity-100 hover:text-gray-500 dark:hover:text-gray-400 transition-opacity"
        title="プロジェクト設定"
      >
        <Settings className="w-3.5 h-3.5" />
      </Link>
    </div>
  )
}

// ── Main Layout ────────────────────────────────────

export default function Layout() {
  const navigate = useNavigate()
  const location = useLocation()
  const qc = useQueryClient()
  const { user, logout } = useAuthStore()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  useSSE()

  // Extract current project ID from URL path (e.g. /projects/:projectId/...)
  const activeProjectId = location.pathname.match(/^\/projects\/([^/]+)/)?.[1] ?? null

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get('/projects').then((r) => r.data),
  })

  const reorderMutation = useMutation({
    mutationFn: (ids: string[]) => api.post('/projects/reorder', { ids }),
    onMutate: async (ids: string[]) => {
      await qc.cancelQueries({ queryKey: ['projects'] })
      const previous = qc.getQueryData<Project[]>(['projects'])
      if (previous) {
        const idOrder = new Map(ids.map((id, i) => [id, i]))
        const sorted = [...previous].sort(
          (a, b) => (idOrder.get(a.id) ?? 0) - (idOrder.get(b.id) ?? 0),
        )
        qc.setQueryData(['projects'], sorted)
      }
      return { previous }
    },
    onError: (_err, _ids, context) => {
      if (context?.previous) {
        qc.setQueryData(['projects'], context.previous)
      }
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }))

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = projects.findIndex((p: Project) => p.id === active.id)
    const newIndex = projects.findIndex((p: Project) => p.id === over.id)
    if (oldIndex < 0 || newIndex < 0) return
    const reordered = arrayMove(projects.map((p: Project) => p.id) as string[], oldIndex, newIndex)
    reorderMutation.mutate(reordered)
  }, [projects, reorderMutation])

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const closeSidebar = () => setSidebarOpen(false)

  const sidebarContent = (
    <>
      <div className="h-14 px-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <CheckSquare className="w-5 h-5 text-terracotta-600 dark:text-terracotta-400" />
          <span className="font-serif font-medium text-gray-900 dark:text-gray-100">MCP Todo</span>
        </div>
        <button
          onClick={closeSidebar}
          className="md:hidden p-1 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"
          aria-label="サイドバーを閉じる"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        <p className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider px-2 mb-2">
          プロジェクト
        </p>
        <Link
          to="/projects"
          onClick={closeSidebar}
          className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <FolderOpen className="w-4 h-4" />
          すべて
        </Link>
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={projects.map((p: Project) => p.id)} strategy={verticalListSortingStrategy}>
            {projects.map((p: Project) => (
              <SortableProjectItem
                key={p.id}
                project={p}
                closeSidebar={closeSidebar}
                isAdmin={!!user?.is_admin}
                isActive={p.id === activeProjectId}
              />
            ))}
          </SortableContext>
        </DndContext>
        <p className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider px-2 mb-2 mt-4">
          その他
        </p>
        <Link
          to="/bookmarks"
          onClick={closeSidebar}
          className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <Bookmark className="w-4 h-4" />
          ブックマーク
        </Link>
        <Link
          to="/knowledge"
          onClick={closeSidebar}
          className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <BookOpen className="w-4 h-4" />
          ナレッジベース
        </Link>
        <Link
          to="/docsites"
          onClick={closeSidebar}
          className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <Library className="w-4 h-4" />
          ドキュメントサイト
        </Link>
        {user?.is_admin && (
          <Link
            to="/workspaces"
            onClick={closeSidebar}
            className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            <TerminalSquare className="w-4 h-4" />
            ワークスペース
          </Link>
        )}
      </nav>

      <div className="px-3 py-4 border-t border-gray-100 dark:border-gray-700 space-y-1">
        <Link
          to="/settings"
          onClick={closeSidebar}
          aria-label="アカウント設定"
          className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <UserCog className="w-4 h-4" />
          アカウント設定
        </Link>
        {user?.is_admin && (
          <Link
            to="/admin"
            onClick={closeSidebar}
            aria-label="管理画面"
            className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            <Settings className="w-4 h-4" />
            管理者設定
          </Link>
        )}
        <button
          onClick={handleLogout}
          aria-label="ログアウト"
          className="w-full flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <LogOut className="w-4 h-4" />
          ログアウト
        </button>
        <div className="px-2 pt-2 flex items-center justify-between">
          <p className="text-xs text-gray-400 dark:text-gray-500 truncate">{user?.name}</p>
          <ThemeToggle />
        </div>
        <p className="text-[9px] text-gray-300 dark:text-gray-600 px-2" title={__BUILD_TIMESTAMP__}>build: {__BUILD_TIMESTAMP__}</p>
      </div>
    </>
  )

  return (
    <div className="flex h-screen bg-gray-50 dark:bg-gray-900">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-56 bg-gray-100 dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex-col">
        {sidebarContent}
      </aside>

      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={closeSidebar}
        />
      )}

      {/* Mobile sidebar drawer */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-56 bg-gray-100 dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col transform transition-transform duration-200 ease-in-out md:hidden ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {sidebarContent}
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-hidden flex flex-col">
        {/* Mobile header with hamburger */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800 md:hidden">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-1 rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            aria-label="メニューを開く"
          >
            <Menu className="w-5 h-5" />
          </button>
          <div className="flex items-center gap-2">
            <CheckSquare className="w-4 h-4 text-terracotta-600 dark:text-terracotta-400" />
            <span className="font-serif font-medium text-sm text-gray-900 dark:text-gray-100">MCP Todo</span>
          </div>
        </div>
        <ErrorBoundary key={location.pathname} fallback={<PageErrorFallback />}>
          <Outlet />
        </ErrorBoundary>
      </main>

      {/* Sprint 2 / S2-8: cross-project Live Activity floating panel */}
      <LiveActivityPanel />
    </div>
  )
}
