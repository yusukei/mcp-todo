import { useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ChevronRight,
  Folder,
  File as FileIcon,
  Home,
  Loader2,
  RefreshCw,
} from 'lucide-react'
import { api } from '../../api/client'
import { showInfoToast } from '../../components/common/Toast'
import type { PaneComponentProps } from '../paneRegistry'
import { useWorkbenchEventBus } from '../eventBus'

interface DirEntry {
  name: string
  type: 'file' | 'dir' | 'directory'
  size?: number | null
  mtime?: number | null
}

interface ListResponse {
  entries: DirEntry[]
  count: number
  path: string
}

const isDir = (e: DirEntry): boolean =>
  e.type === 'dir' || e.type === 'directory'

const joinPath = (a: string, b: string): string => {
  if (a === '.' || a === '' || a === '/') return b
  if (a.endsWith('/')) return a + b
  return `${a}/${b}`
}

const parentPath = (p: string): string => {
  if (!p || p === '.' || p === '/') return '.'
  const parts = p.split('/').filter(Boolean)
  if (parts.length <= 1) return '.'
  return parts.slice(0, -1).join('/')
}

const breadcrumbs = (p: string): { name: string; path: string }[] => {
  if (!p || p === '.') return []
  const parts = p.split('/').filter(Boolean)
  return parts.map((name, i) => ({
    name,
    path: parts.slice(0, i + 1).join('/'),
  }))
}

const formatSize = (bytes: number | null | undefined): string => {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

const isMarkdown = (name: string): boolean =>
  /\.(md|markdown)$/i.test(name)

/**
 * File browser pane (PR2b). Reuses the existing
 * ``/api/v1/workspaces/projects/{id}/files`` endpoint that powers
 * the ProjectFileBrowserTab; this pane just lays it out for the
 * Workbench's per-pane state model.
 *
 * paneConfig: ``{ cwd?: string }`` (relative to ``project.remote.remote_path``).
 *
 * Cross-pane wiring (PR3):
 *  - directory cmd/ctrl-click → ``open-terminal-cwd`` event so the
 *    active TerminalPane runs ``cd "<path>"``.
 *  - markdown file click currently surfaces an info toast: project
 *    documents (DocPane) are independent records and do not have a
 *    file-system path field, so an automatic markdown↔doc mapping
 *    isn't possible without a backend schema change. Tracked for a
 *    follow-up.
 *
 * The pane is intentionally minimal — preview / context menu /
 * git status views are out of scope; the full feature set lives in
 * the standalone Project's File Browser tab.
 */
export default function FileBrowserPane({
  paneId,
  projectId,
  paneConfig,
  onConfigChange,
}: PaneComponentProps) {
  void paneId // FileBrowserPane only emits; no subscriptions.
  const config = paneConfig as { cwd?: string }
  const cwd = config.cwd ?? '.'
  const bus = useWorkbenchEventBus()

  const list = useQuery<ListResponse>({
    queryKey: ['workspace-files', projectId, cwd],
    queryFn: () =>
      api
        .get(`/workspaces/projects/${projectId}/files`, {
          params: { path: cwd },
        })
        .then((r) => r.data),
    retry: false,
  })

  const navigateTo = useCallback(
    (path: string) => {
      onConfigChange({ cwd: path })
    },
    [onConfigChange],
  )

  const handleEntryClick = useCallback(
    (entry: DirEntry, ev: React.MouseEvent) => {
      const fullPath = cwd === '.' || cwd === ''
        ? entry.name
        : joinPath(cwd, entry.name)

      if (isDir(entry)) {
        if (ev.metaKey || ev.ctrlKey) {
          // Cmd/Ctrl+click on a directory: route a ``cd <path>`` to
          // the active TerminalPane via the workbench event bus.
          // The bus picks the focused-or-most-recent terminal pane,
          // or surfaces a "no terminal pane" toast if none exist.
          bus.emit('open-terminal-cwd', { cwd: fullPath })
          return
        }
        navigateTo(fullPath)
        return
      }
      // Markdown files in the workspace filesystem aren't linked to
      // ``ProjectDocument`` records (those are stored in Mongo and
      // have no ``path`` field). Surface that limitation rather
      // than emitting an event we can't service correctly.
      if (isMarkdown(entry.name)) {
        showInfoToast(
          `Workspace markdown files are not yet linked to project Docs. Open ${entry.name} via the file viewer.`,
        )
      } else {
        showInfoToast(
          `File preview is not yet implemented for ${entry.name}.`,
        )
      }
    },
    [cwd, navigateTo, bus],
  )

  const crumbs = breadcrumbs(cwd)
  const status = (
    list.error as { response?: { status?: number } } | undefined
  )?.response?.status

  return (
    <div className="h-full flex flex-col">
      {/* Breadcrumb header */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-line-1 text-xs overflow-x-auto">
        <button
          type="button"
          onClick={() => navigateTo('.')}
          className="flex items-center gap-1 text-gray-300 hover:text-gray-50 flex-shrink-0"
          title="Project root"
        >
          <Home className="w-3 h-3" />
          root
        </button>
        {crumbs.map((c) => (
          <div key={c.path} className="flex items-center gap-1 flex-shrink-0">
            <ChevronRight className="w-3 h-3 text-gray-300" />
            <button
              type="button"
              onClick={() => navigateTo(c.path)}
              className="text-gray-200 hover:text-gray-50 font-mono"
            >
              {c.name}
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => list.refetch()}
          className="ml-auto text-gray-300 hover:text-gray-50 flex-shrink-0"
          title="Refresh"
        >
          <RefreshCw
            className={`w-3 h-3 ${list.isFetching ? 'animate-spin' : ''}`}
          />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-auto">
        {list.isLoading ? (
          <div className="p-6 text-center text-gray-300">
            <Loader2 className="w-5 h-5 animate-spin mx-auto" />
          </div>
        ) : list.isError ? (
          <ErrorState
            status={status}
            cwd={cwd}
            onUp={() => navigateTo(parentPath(cwd))}
            onRetry={() => list.refetch()}
          />
        ) : (
          <FileList
            entries={list.data?.entries ?? []}
            cwd={cwd}
            onUp={() => navigateTo(parentPath(cwd))}
            onClickEntry={handleEntryClick}
          />
        )}
      </div>
    </div>
  )
}

interface FileListProps {
  entries: DirEntry[]
  cwd: string
  onUp: () => void
  onClickEntry: (e: DirEntry, ev: React.MouseEvent) => void
}

function FileList({ entries, cwd, onUp, onClickEntry }: FileListProps) {
  const sorted = [...entries].sort((a, b) => {
    const aDir = isDir(a)
    const bDir = isDir(b)
    if (aDir !== bDir) return aDir ? -1 : 1
    return a.name.localeCompare(b.name)
  })
  return (
    <ul className="divide-y divide-gray-800 text-sm font-mono">
      {cwd !== '.' && cwd !== '' && (
        <li>
          <button
            type="button"
            onClick={onUp}
            className="w-full text-left px-3 py-1.5 hover:bg-gray-700/40 flex items-center gap-2 text-gray-300"
          >
            <Folder className="w-4 h-4" />
            <span>..</span>
          </button>
        </li>
      )}
      {sorted.length === 0 && (
        <li className="px-3 py-4 text-center text-xs text-gray-300">
          (empty directory)
        </li>
      )}
      {sorted.map((entry) => (
        <li key={entry.name}>
          <button
            type="button"
            onClick={(ev) => onClickEntry(entry, ev)}
            className="w-full text-left px-3 py-1.5 hover:bg-gray-700/40 flex items-center gap-2"
            title={
              isDir(entry)
                ? 'Open directory (Cmd+click → cd in terminal pane)'
                : isMarkdown(entry.name)
                  ? 'Markdown — click opens in Doc pane (PR3)'
                  : 'File'
            }
          >
            {isDir(entry) ? (
              <Folder className="w-4 h-4 text-status-progress flex-shrink-0" />
            ) : (
              <FileIcon
                className={`w-4 h-4 flex-shrink-0 ${
                  isMarkdown(entry.name)
                    ? 'text-status-done'
                    : 'text-gray-300'
                }`}
              />
            )}
            <span className="flex-1 truncate text-gray-50">
              {entry.name}
            </span>
            {!isDir(entry) && (
              <span className="text-[10px] text-gray-300 flex-shrink-0">
                {formatSize(entry.size)}
              </span>
            )}
          </button>
        </li>
      ))}
    </ul>
  )
}

interface ErrorStateProps {
  status: number | undefined
  cwd: string
  onUp: () => void
  onRetry: () => void
}

function ErrorState({ status, cwd, onUp, onRetry }: ErrorStateProps) {
  // Not-found / no-binding paths bounce the user to a recoverable
  // location instead of leaving them stuck.
  if (status === 404) {
    return (
      <div className="p-6 text-center text-sm text-status-hold">
        <p>
          Path <code className="font-mono">{cwd}</code> is missing.
        </p>
        <button
          type="button"
          onClick={onUp}
          className="mt-3 text-xs px-3 py-1 rounded-comfortable bg-status-hold/15 hover:bg-status-hold/25"
        >
          Up to parent
        </button>
      </div>
    )
  }
  if (status === 409) {
    return (
      <div className="p-6 text-center text-sm text-status-hold">
        <p>
          The agent is offline. Try again once it reconnects.
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 text-xs px-3 py-1 rounded-comfortable bg-status-hold/15 hover:bg-status-hold/25"
        >
          Retry
        </button>
      </div>
    )
  }
  return (
    <div className="p-6 text-center text-sm text-pri-urgent">
      <p>Failed to list files{status ? ` (HTTP ${status})` : ''}.</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 text-xs px-3 py-1 rounded-comfortable bg-pri-urgent/15 hover:bg-pri-urgent/25"
      >
        Retry
      </button>
    </div>
  )
}
