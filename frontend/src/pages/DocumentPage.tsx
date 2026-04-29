import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { api } from '../api/client'
import ProjectDocumentsTab from '../components/project/ProjectDocumentsTab'
import CopyUrlButton from '../components/common/CopyUrlButton'

export default function DocumentPage() {
  const { projectId, documentId } = useParams<{ projectId: string; documentId: string }>()
  const navigate = useNavigate()

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.get(`/projects/${projectId}`).then((r) => r.data),
    enabled: !!projectId,
  })

  return (
    <div className="flex flex-col h-full">
      <div className="px-8 py-4 border-b border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800 flex items-center gap-3">
        <button
          onClick={() => navigate(`/projects/${projectId}`)}
          className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-600 dark:hover:text-gray-200"
          title="プロジェクトに戻る"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        {project && (
          <>
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: project.color }} />
            <h1 className="text-xl font-serif font-medium text-gray-800 dark:text-gray-100">{project.name}</h1>
          </>
        )}
        <span className="text-gray-400 dark:text-gray-500">/ ドキュメント</span>
        {projectId && documentId && (
          <div className="ml-auto flex-shrink-0">
            <CopyUrlButton
              kind="document_full"
              contextProjectId={projectId}
              resourceId={documentId}
              title={project?.name ?? 'document'}
              variant="always-visible"
              size="md"
            />
          </div>
        )}
      </div>
      <div className="flex-1 overflow-y-auto">
        <ProjectDocumentsTab projectId={projectId!} initialDocumentId={documentId} />
      </div>
    </div>
  )
}
