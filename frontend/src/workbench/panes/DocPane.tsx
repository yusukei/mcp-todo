import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ChevronDown,
  ExternalLink,
  FileText,
  Loader2,
} from 'lucide-react'
import { api } from '../../api/client'
import MarkdownRenderer from '../../components/common/MarkdownRenderer'
import type { PaneComponentProps } from '../paneRegistry'
import { useWorkbenchEvent } from '../eventBus'

interface DocSummary {
  id: string
  title: string
  category: string
  updated_at: string
}

interface DocDetail extends DocSummary {
  content: string
  tags: string[]
}

interface DocListResponse {
  items: DocSummary[]
  total: number
}

/**
 * Render the project's currently-selected document. The doc id is
 * stored in ``paneConfig.docId``; when absent the pane shows a
 * picker that lets the user choose one. Cross-pane events (PR3)
 * will also call ``onConfigChange({ docId })`` so a click in the
 * Tasks pane swaps the DocPane to the linked doc.
 *
 * Stale id handling: a 404 from the detail endpoint flips back to
 * the picker rather than crashing the pane (the user could have
 * deleted the doc in another tab between mount and refetch).
 */
export default function DocPane({
  paneId,
  projectId,
  paneConfig,
  onConfigChange,
}: PaneComponentProps) {
  const config = paneConfig as { docId?: string }
  const [pickerOpen, setPickerOpen] = useState(!config.docId)

  // Cross-pane wiring: a click in TasksPane / FileBrowserPane emits
  // ``open-doc``; the workbench event bus picks one DocPane to route
  // to (focused → most-recent → first). On receipt we just patch
  // paneConfig — the existing ``useQuery`` reacts to the new docId.
  useWorkbenchEvent(paneId, 'open-doc', ({ docId }) => {
    if (!docId || docId === config.docId) return
    onConfigChange({ docId })
    setPickerOpen(false)
  })

  const docList = useQuery<DocListResponse>({
    queryKey: ['documents', projectId, 'workbench-picker'],
    queryFn: () =>
      api.get(`/projects/${projectId}/documents/`).then((r) => r.data),
    enabled: pickerOpen,
  })

  const doc = useQuery<DocDetail>({
    queryKey: ['document', projectId, config.docId],
    queryFn: () =>
      api
        .get(`/projects/${projectId}/documents/${config.docId}`)
        .then((r) => r.data),
    enabled: !!config.docId,
    retry: false,
  })

  // ── Picker (no doc selected) ──────────────────────────────────
  if (!config.docId) {
    return (
      <div className="h-full flex flex-col">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-line-1 text-xs text-gray-300">
          <FileText className="w-3.5 h-3.5" />
          Doc — pick a document below
        </div>
        <div className="flex-1 overflow-auto">
          <DocPicker
            list={docList.data?.items}
            isLoading={docList.isLoading}
            isError={docList.isError}
            onPick={(id) => {
              onConfigChange({ docId: id })
              setPickerOpen(false)
            }}
          />
        </div>
      </div>
    )
  }

  // ── 404 → bounce to picker (paneConfig stays for back-button UX) ─
  const status = (
    doc.error as { response?: { status?: number } } | undefined
  )?.response?.status
  if (doc.isError && status === 404) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3 p-6 text-center bg-status-hold/10">
        <AlertTriangle className="w-8 h-8 text-status-hold" />
        <p className="text-sm text-status-hold font-medium font-serif">
          Document no longer exists
        </p>
        <p className="text-xs text-gray-300">
          (id: <code className="font-mono">{config.docId}</code>)
        </p>
        <button
          type="button"
          onClick={() => onConfigChange({ docId: undefined })}
          className="text-xs px-3 py-1.5 rounded-comfortable bg-status-hold/15 text-status-hold hover:bg-status-hold/25"
        >
          Pick another document
        </button>
      </div>
    )
  }

  if (doc.isLoading) {
    return (
      <div className="h-full flex items-center justify-center text-gray-300">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    )
  }

  if (doc.isError) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 p-6 text-center text-pri-urgent text-sm">
        Failed to load document.
        <button
          type="button"
          onClick={() => doc.refetch()}
          className="text-xs px-3 py-1 rounded-comfortable bg-pri-urgent/15 hover:bg-pri-urgent/25"
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-line-1 text-xs">
        <div className="flex items-center gap-2 min-w-0">
          <FileText className="w-3.5 h-3.5 text-gray-300 flex-shrink-0" />
          <span className="truncate text-gray-50 font-serif">
            {doc.data!.title}
          </span>
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-gray-700 text-gray-200 font-mono">
            {doc.data!.category}
          </span>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            type="button"
            onClick={() => onConfigChange({ docId: undefined })}
            className="text-xs text-gray-300 hover:text-gray-50 flex items-center gap-0.5"
            title="Pick a different document"
          >
            <ChevronDown className="w-3 h-3" />
            Switch
          </button>
          <Link
            to={`/projects/${projectId}/documents/${config.docId}`}
            className="text-xs text-gray-300 hover:text-gray-50 flex items-center gap-0.5"
            title="Open in full document page (edit, history)"
          >
            <ExternalLink className="w-3 h-3" />
            Open
          </Link>
        </div>
      </div>
      <div className="flex-1 overflow-auto px-6 py-4 prose dark:prose-invert max-w-none">
        <MarkdownRenderer>{doc.data!.content}</MarkdownRenderer>
      </div>
    </div>
  )
}

interface DocPickerProps {
  list: DocSummary[] | undefined
  isLoading: boolean
  isError: boolean
  onPick: (id: string) => void
}

function DocPicker({ list, isLoading, isError, onPick }: DocPickerProps) {
  if (isLoading) {
    return (
      <div className="p-6 text-center text-gray-300">
        <Loader2 className="w-5 h-5 animate-spin mx-auto" />
      </div>
    )
  }
  if (isError) {
    return (
      <p className="p-6 text-sm text-pri-urgent text-center">
        Failed to load document list.
      </p>
    )
  }
  const items = list ?? []
  if (items.length === 0) {
    return (
      <p className="p-6 text-sm text-gray-300 text-center">
        No documents in this project yet.
      </p>
    )
  }
  return (
    <ul className="divide-y divide-gray-700">
      {items.map((d) => (
        <li key={d.id}>
          <button
            type="button"
            onClick={() => onPick(d.id)}
            className="w-full text-left px-3 py-2 hover:bg-gray-700/40 flex items-center gap-2"
          >
            <FileText className="w-3.5 h-3.5 text-gray-300 flex-shrink-0" />
            <span className="flex-1 truncate text-sm text-gray-50">
              {d.title}
            </span>
            <span className="px-1.5 py-0.5 rounded text-[10px] bg-gray-700 text-gray-200 font-mono">
              {d.category}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
