import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { projectsApi } from '../api'
import { FolderOpen } from 'lucide-react'
import type { Project } from '../types'

export default function ProjectsPage() {
  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
  })

  if (isLoading) return <div className="p-8 text-gray-500 dark:text-gray-400">読み込み中...</div>

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-serif font-medium text-gray-800 dark:text-gray-100">プロジェクト</h1>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {projects.map((p: Project) => (
          <Link
            key={p.id}
            to={`/projects/${p.id}`}
            className="bg-gray-100 dark:bg-gray-800 rounded-generous border border-gray-200 dark:border-gray-700 p-5 hover:shadow-whisper transition-shadow"
          >
            <div className="flex items-center gap-3 mb-3">
              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: p.color ?? undefined }} />
              <span className="font-serif font-medium text-gray-900 dark:text-gray-100">{p.name}</span>
            </div>
            {p.description && (
              <p className="text-sm text-gray-500 dark:text-gray-400 line-clamp-2">{p.description}</p>
            )}
            <div className="mt-3 text-xs text-gray-400 dark:text-gray-500">
              メンバー {p.members?.length ?? 0}人
            </div>
          </Link>
        ))}
        {projects.length === 0 && (
          <div className="col-span-3 text-center py-16 text-gray-400 dark:text-gray-500">
            <FolderOpen className="w-12 h-12 mx-auto mb-3 opacity-40" />
            <p>プロジェクトがありません</p>
          </div>
        )}
      </div>
    </div>
  )
}
