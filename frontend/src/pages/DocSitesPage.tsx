import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Library, ExternalLink } from 'lucide-react'
import { api } from '../api/client'
import type { DocSite } from '../types'

export default function DocSitesPage() {
  const { data: sites = [], isLoading } = useQuery<DocSite[]>({
    queryKey: ['docsites'],
    queryFn: () => api.get('/docsites').then((r) => r.data),
  })

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <Library className="w-6 h-6 text-terracotta-600 dark:text-terracotta-400" />
          <h1 className="text-xl font-serif font-medium text-gray-800 dark:text-gray-100">ドキュメントサイト</h1>
        </div>

        {isLoading ? (
          <p className="text-gray-500 dark:text-gray-400">読み込み中...</p>
        ) : sites.length === 0 ? (
          <div className="text-center py-12">
            <Library className="w-12 h-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
            <p className="text-gray-500 dark:text-gray-400">ドキュメントサイトがありません</p>
            <p className="text-sm text-gray-400 dark:text-gray-500 mt-1">
              CLIの import-docsite コマンドでインポートできます
            </p>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {sites.map((site) => (
              <Link
                key={site.id}
                to={`/docsites/${site.id}`}
                className="block p-5 bg-gray-100 dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-terracotta-300 dark:hover:border-terracotta-600 hover:shadow-md transition-all"
              >
                <div className="flex items-start gap-3">
                  <Library className="w-5 h-5 text-terracotta-500 dark:text-terracotta-400 mt-0.5 flex-shrink-0" />
                  <div className="min-w-0">
                    <h2 className="font-semibold text-gray-800 dark:text-gray-100 truncate">{site.name}</h2>
                    {site.description && (
                      <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">{site.description}</p>
                    )}
                    <div className="flex items-center gap-3 mt-2 text-xs text-gray-400 dark:text-gray-500">
                      <span>{site.page_count} ページ</span>
                      {site.source_url && (
                        <span className="flex items-center gap-1">
                          <ExternalLink className="w-3 h-3" />
                          ソースあり
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
