import { Link, Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { FolderOpen, LogOut, Settings, CheckSquare } from 'lucide-react'
import { api } from '../../api/client'
import { useAuthStore } from '../../store/auth'
import { useSSE } from '../../hooks/useSSE'

export default function Layout() {
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  useSSE()

  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get('/projects').then((r) => r.data),
  })

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-gray-200 flex flex-col">
        <div className="px-4 py-5 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <CheckSquare className="w-5 h-5 text-indigo-600" />
            <span className="font-bold text-gray-800">Claude Todo</span>
          </div>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider px-2 mb-2">
            プロジェクト
          </p>
          <Link
            to="/projects"
            className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100"
          >
            <FolderOpen className="w-4 h-4" />
            すべて
          </Link>
          {projects.map((p: any) => (
            <Link
              key={p.id}
              to={`/projects/${p.id}`}
              className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100"
            >
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: p.color }} />
              <span className="truncate">{p.name}</span>
            </Link>
          ))}
        </nav>

        <div className="px-3 py-4 border-t border-gray-100 space-y-1">
          {user?.is_admin && (
            <Link
              to="/admin"
              className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100"
            >
              <Settings className="w-4 h-4" />
              管理者設定
            </Link>
          )}
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100"
          >
            <LogOut className="w-4 h-4" />
            ログアウト
          </button>
          <div className="px-2 pt-2">
            <p className="text-xs text-gray-400 truncate">{user?.name}</p>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-hidden flex flex-col">
        <Outlet />
      </main>
    </div>
  )
}
