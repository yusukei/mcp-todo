import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { FolderOpen, Plus } from 'lucide-react'

export default function ProjectsPage() {
  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get('/projects').then((r) => r.data),
  })

  if (isLoading) return <div className="p-8 text-gray-500">読み込み中...</div>

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-800">プロジェクト</h1>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {projects.map((p: any) => (
          <Link
            key={p.id}
            to={`/projects/${p.id}`}
            className="bg-white rounded-xl border border-gray-200 p-5 hover:shadow-md transition-shadow"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: p.color }} />
              <span className="font-semibold text-gray-800">{p.name}</span>
            </div>
            {p.description && (
              <p className="text-sm text-gray-500 line-clamp-2">{p.description}</p>
            )}
            <div className="mt-3 text-xs text-gray-400">
              メンバー {p.members?.length ?? 0}人
            </div>
          </Link>
        ))}
        {projects.length === 0 && (
          <div className="col-span-3 text-center py-16 text-gray-400">
            <FolderOpen className="w-12 h-12 mx-auto mb-3 opacity-40" />
            <p>プロジェクトがありません</p>
          </div>
        )}
      </div>
    </div>
  )
}
