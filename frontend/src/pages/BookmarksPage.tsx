import { useQuery } from '@tanstack/react-query'
import { useParams, useNavigate } from 'react-router-dom'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { api } from '../api/client'
import ProjectBookmarksTab from '../components/project/ProjectBookmarksTab'

interface CommonProject {
  id: string
  name: string
  hidden?: boolean
}

/**
 * Standalone Bookmarks page.
 *
 * Bookmarks are persisted as ordinary `bookmark` documents tied to a
 * `project_id`, but the UI presents them as a global feature. To bridge
 * the gap, every bookmark is stored under the singleton hidden "Common"
 * project. This page resolves the Common project once and delegates
 * rendering to the existing `ProjectBookmarksTab` component, so we get
 * the same UI / collection sidebar / detail panel without duplicating
 * code or migrating the bookmark data model.
 */
export default function BookmarksPage() {
  const { bookmarkId } = useParams<{ bookmarkId?: string }>()
  const navigate = useNavigate()

  const { data: commonProject, isError, isLoading } = useQuery<CommonProject>({
    queryKey: ['common-project'],
    queryFn: () => api.get('/projects/common').then((r) => r.data),
    retry: false,
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        読み込み中...
      </div>
    )
  }

  if (isError || !commonProject) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 dark:text-gray-400 gap-3 px-6 text-center">
        <AlertTriangle className="w-10 h-10 text-amber-500" />
        <p className="text-sm font-medium">Common プロジェクトが未設定です</p>
        <p className="text-xs max-w-md">
          ブックマーク機能は隠しの "Common" プロジェクトに保存されます。<br />
          管理者に <code className="px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono">python -m app.cli setup-common-project --rename-from &lt;legacy_project_id&gt;</code> の実行を依頼してください。
        </p>
      </div>
    )
  }

  return (
    <ProjectBookmarksTab
      projectId={commonProject.id}
      selectedId={bookmarkId ?? null}
      onSelectId={(id) => {
        if (id) navigate(`/bookmarks/${id}`)
        else navigate('/bookmarks')
      }}
    />
  )
}
