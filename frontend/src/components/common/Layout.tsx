import { useState } from 'react'
import { Link, Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { FolderOpen, LogOut, Settings, UserCog, CheckSquare, Menu, X, BookOpen, Library } from 'lucide-react'
import { api } from '../../api/client'
import { useAuthStore } from '../../store/auth'
import { useSSE } from '../../hooks/useSSE'
import ThemeToggle from './ThemeToggle'
import type { Project } from '../../types'

export default function Layout() {
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  useSSE()

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get('/projects').then((r) => r.data),
  })

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const closeSidebar = () => setSidebarOpen(false)

  const sidebarContent = (
    <>
      <div className="px-4 py-5 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <CheckSquare className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
          <span className="font-bold text-gray-800 dark:text-gray-100">MCP Todo</span>
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
        {projects.map((p: Project) => (
          <div key={p.id} className="group/project flex items-center rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700">
            <Link
              to={`/projects/${p.id}`}
              onClick={closeSidebar}
              className="flex-1 flex items-center gap-2 px-2 py-2 text-sm text-gray-600 dark:text-gray-300 min-w-0"
            >
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: p.color ?? undefined }} />
              <span className="truncate">{p.name}</span>
            </Link>
            <Link
              to={`/projects/${p.id}/settings`}
              onClick={closeSidebar}
              className="flex-shrink-0 p-1.5 mr-1 rounded text-gray-300 dark:text-gray-600 opacity-0 group-hover/project:opacity-100 hover:text-gray-500 dark:hover:text-gray-400 transition-opacity"
              title="プロジェクト設定"
            >
              <Settings className="w-3.5 h-3.5" />
            </Link>
          </div>
        ))}
        <p className="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider px-2 mb-2 mt-4">
          その他
        </p>
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
      </div>
    </>
  )

  return (
    <div className="flex h-screen bg-gray-50 dark:bg-gray-900">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-56 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex-col">
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
        className={`fixed inset-y-0 left-0 z-50 w-56 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col transform transition-transform duration-200 ease-in-out md:hidden ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        {sidebarContent}
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-hidden flex flex-col">
        {/* Mobile header with hamburger */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 md:hidden">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-1 rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            aria-label="メニューを開く"
          >
            <Menu className="w-5 h-5" />
          </button>
          <div className="flex items-center gap-2">
            <CheckSquare className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
            <span className="font-semibold text-sm text-gray-800 dark:text-gray-100">MCP Todo</span>
          </div>
        </div>
        <Outlet />
      </main>
    </div>
  )
}
