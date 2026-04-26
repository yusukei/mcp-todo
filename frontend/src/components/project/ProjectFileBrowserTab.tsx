import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Folder,
  FolderOpen,
  File,
  ChevronRight,
  RefreshCw,
  GitBranch,
  AlertCircle,
  Loader2,
  ExternalLink,
} from 'lucide-react'
import { api } from '../../api/client'
import MarkdownRenderer from '../common/MarkdownRenderer'

// ── Types ─────────────────────────────────────────────────────

interface DirEntry {
  name: string
  type: 'file' | 'dir' | 'directory'
  size?: number
  mtime?: string
}

interface GitFile {
  status: string
  path: string
}

// ── Helpers ───────────────────────────────────────────────────

function statusColor(xy: string): string {
  const x = xy[0] ?? ' '
  const y = xy[1] ?? ' '
  if (x === 'A' || y === 'A') return 'text-green-600 dark:text-green-400'
  if (x === 'D' || y === 'D') return 'text-red-500 dark:text-red-400'
  if (x === 'R' || y === 'R') return 'text-blue-500 dark:text-blue-400'
  if (x === '?' || y === '?') return 'text-gray-400 dark:text-gray-500'
  return 'text-amber-500 dark:text-amber-400'
}

function statusLabel(xy: string): string {
  const map: Record<string, string> = {
    M: 'M', A: 'A', D: 'D', R: 'R', C: 'C', U: 'U', '?': '?', '!': '!',
  }
  const x = xy[0] ?? ' '
  const y = xy[1] ?? ' '
  if (x !== ' ' && x !== '?') return map[x] ?? x
  return map[y] ?? y
}

function joinPath(base: string, name: string): string {
  if (base === '.' || base === '') return name
  return `${base}/${name}`
}

// ── Diff renderer ─────────────────────────────────────────────

function DiffViewer({ diff }: { diff: string }) {
  if (!diff) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 dark:text-gray-500 text-sm">
        差分はありません
      </div>
    )
  }
  return (
    <pre className="text-xs font-mono overflow-auto h-full p-4 leading-5">
      {diff.split('\n').map((line, i) => {
        let cls = 'text-gray-700 dark:text-gray-300'
        if (line.startsWith('+') && !line.startsWith('+++')) cls = 'text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-950/30'
        else if (line.startsWith('-') && !line.startsWith('---')) cls = 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30'
        else if (line.startsWith('@@')) cls = 'text-blue-600 dark:text-blue-400'
        else if (line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('+++') || line.startsWith('---')) cls = 'text-gray-500 dark:text-gray-400'
        return (
          <span key={i} className={`block ${cls}`}>{line || ' '}</span>
        )
      })}
    </pre>
  )
}

// ── File type helpers ─────────────────────────────────────────

function fileExt(path: string): string {
  return path.split('.').pop()?.toLowerCase() ?? ''
}

function isMarkdown(path: string): boolean {
  return ['md', 'markdown'].includes(fileExt(path))
}

function isPdf(path: string): boolean {
  return fileExt(path) === 'pdf'
}

// ── File content viewer ───────────────────────────────────────

function FileViewer({
  content,
  truncated,
  isBinary,
  path,
  projectId,
}: {
  content: string
  truncated: boolean
  isBinary: boolean
  path: string
  projectId: string
}) {
  if (isPdf(path)) {
    const rawUrl = `/api/v1/workspaces/projects/${projectId}/file-raw?path=${encodeURIComponent(path)}`
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-center justify-end px-3 py-1.5 border-b border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 shrink-0">
          <a
            href={rawUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs text-accent-600 dark:text-accent-400 hover:underline"
          >
            <ExternalLink className="w-3 h-3" />
            ブラウザで開く
          </a>
        </div>
        <iframe
          src={rawUrl}
          className="flex-1 w-full border-0"
          title={path}
        />
      </div>
    )
  }

  if (isBinary) {
    const rawUrl = `/api/v1/workspaces/projects/${projectId}/file-raw?path=${encodeURIComponent(path)}`
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400 dark:text-gray-500">
        <File className="w-10 h-10 opacity-30" />
        <p className="text-sm">バイナリファイルはインライン表示できません</p>
        <a
          href={rawUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1 text-xs text-accent-600 dark:text-accent-400 hover:underline"
        >
          <ExternalLink className="w-3 h-3" />
          ダウンロード / ブラウザで開く
        </a>
      </div>
    )
  }

  if (isMarkdown(path)) {
    return (
      <div className="h-full overflow-auto px-6 py-5">
        <MarkdownRenderer>{content}</MarkdownRenderer>
        {truncated && (
          <p className="mt-4 text-xs text-amber-600 dark:text-amber-400">
            [ファイルが大きすぎるため一部省略されています]
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="h-full overflow-auto">
      <pre className="text-xs font-mono p-4 leading-5 text-gray-800 dark:text-gray-200">
        {content}
      </pre>
      {truncated && (
        <div className="px-4 pb-4 text-xs text-amber-600 dark:text-amber-400">
          [ファイルが大きすぎるため一部省略されています]
        </div>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────

export default function ProjectFileBrowserTab({ projectId }: { projectId: string }) {
  const [activeTab, setActiveTab] = useState<'files' | 'git'>('files')
  const [currentPath, setCurrentPath] = useState('.')
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [selectedGitFile, setSelectedGitFile] = useState<string | null>(null)

  // Breadcrumb segments from currentPath
  const breadcrumbs = useMemo(() => {
    if (currentPath === '.') return []
    return currentPath.split('/').filter(Boolean)
  }, [currentPath])

  // ── Queries ──────────────────────────────────────────────────

  const {
    data: dirData,
    isLoading: dirLoading,
    error: dirError,
    refetch: refetchDir,
  } = useQuery({
    queryKey: ['workspace-files', projectId, currentPath],
    queryFn: () =>
      api.get(`/workspaces/projects/${projectId}/files`, { params: { path: currentPath } }).then((r) => r.data),
    enabled: activeTab === 'files',
  })

  const { data: fileData, isLoading: fileLoading } = useQuery({
    queryKey: ['workspace-file', projectId, selectedFile],
    queryFn: () =>
      api.get(`/workspaces/projects/${projectId}/file`, { params: { path: selectedFile } }).then((r) => r.data),
    enabled: !!selectedFile && activeTab === 'files',
  })

  const {
    data: gitStatusData,
    isLoading: gitLoading,
    refetch: refetchGit,
  } = useQuery({
    queryKey: ['workspace-git-status', projectId],
    queryFn: () =>
      api.get(`/workspaces/projects/${projectId}/git/status`).then((r) => r.data),
    enabled: activeTab === 'git',
  })

  const { data: diffData, isLoading: diffLoading } = useQuery({
    queryKey: ['workspace-git-diff', projectId, selectedGitFile],
    queryFn: () =>
      api
        .get(`/workspaces/projects/${projectId}/git/diff`, {
          params: selectedGitFile ? { path: selectedGitFile } : {},
        })
        .then((r) => r.data),
    enabled: activeTab === 'git',
  })

  // ── Handlers ─────────────────────────────────────────────────

  const navigateTo = (path: string) => {
    setCurrentPath(path)
    setSelectedFile(null)
  }

  const handleEntryClick = (entry: DirEntry) => {
    const isDir = entry.type === 'dir' || entry.type === 'directory'
    const fullPath = joinPath(currentPath, entry.name)
    if (isDir) {
      navigateTo(fullPath)
    } else {
      setSelectedFile(fullPath)
    }
  }

  const navigateToBreadcrumb = (index: number) => {
    if (index < 0) {
      navigateTo('.')
    } else {
      navigateTo(breadcrumbs.slice(0, index + 1).join('/'))
    }
  }

  const entries: DirEntry[] = dirData?.entries ?? []
  const gitFiles: GitFile[] = gitStatusData?.files ?? []

  // ── Render ────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      {/* Inner tab switcher */}
      <div className="flex items-center gap-1 px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 shrink-0">
        <button
          onClick={() => setActiveTab('files')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            activeTab === 'files'
              ? 'bg-white dark:bg-gray-700 text-accent-600 dark:text-accent-400 shadow-sm'
              : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
          }`}
        >
          <FolderOpen className="w-3.5 h-3.5" />
          ファイル
        </button>
        <button
          onClick={() => setActiveTab('git')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
            activeTab === 'git'
              ? 'bg-white dark:bg-gray-700 text-accent-600 dark:text-accent-400 shadow-sm'
              : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
          }`}
        >
          <GitBranch className="w-3.5 h-3.5" />
          Git 変更
          {gitFiles.length > 0 && (
            <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-400">
              {gitFiles.length}
            </span>
          )}
        </button>
        <div className="ml-auto">
          <button
            onClick={() => (activeTab === 'files' ? refetchDir() : refetchGit())}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            title="更新"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex flex-1 overflow-hidden">
        {activeTab === 'files' ? (
          <>
            {/* Left: Directory listing */}
            <div className="w-64 flex-shrink-0 border-r border-gray-200 dark:border-gray-700 flex flex-col overflow-hidden">
              {/* Breadcrumb */}
              <div className="flex items-center gap-1 px-3 py-2 text-xs text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-gray-700 flex-wrap min-h-[36px]">
                <button
                  onClick={() => navigateToBreadcrumb(-1)}
                  className="hover:text-gray-800 dark:hover:text-gray-200 font-mono"
                >
                  root
                </button>
                {breadcrumbs.map((seg, i) => (
                  <span key={i} className="flex items-center gap-1">
                    <ChevronRight className="w-3 h-3" />
                    <button
                      onClick={() => navigateToBreadcrumb(i)}
                      className="hover:text-gray-800 dark:hover:text-gray-200 font-mono"
                    >
                      {seg}
                    </button>
                  </span>
                ))}
              </div>

              {/* File list */}
              <div className="flex-1 overflow-y-auto">
                {dirLoading ? (
                  <div className="flex items-center justify-center py-8 text-gray-400">
                    <Loader2 className="w-4 h-4 animate-spin" />
                  </div>
                ) : dirError ? (
                  <div className="flex items-center gap-2 p-4 text-red-500 dark:text-red-400 text-xs">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    読み込みエラー
                  </div>
                ) : (
                  <>
                    {currentPath !== '.' && (
                      <button
                        onClick={() => {
                          const parts = currentPath.split('/')
                          parts.pop()
                          navigateTo(parts.join('/') || '.')
                        }}
                        className="w-full flex items-center gap-2 px-3 py-1.5 text-sm text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700/50"
                      >
                        <Folder className="w-4 h-4" />
                        ..
                      </button>
                    )}
                    {entries
                      .slice()
                      .sort((a, b) => {
                        const aDir = a.type === 'dir' || a.type === 'directory'
                        const bDir = b.type === 'dir' || b.type === 'directory'
                        if (aDir !== bDir) return aDir ? -1 : 1
                        return a.name.localeCompare(b.name)
                      })
                      .map((entry) => {
                        const isDir = entry.type === 'dir' || entry.type === 'directory'
                        const fullPath = joinPath(currentPath, entry.name)
                        const isSelected = selectedFile === fullPath
                        return (
                          <button
                            key={entry.name}
                            onClick={() => handleEntryClick(entry)}
                            className={`w-full flex items-center gap-2 px-3 py-1.5 text-sm transition-colors text-left ${
                              isSelected
                                ? 'bg-accent-50 dark:bg-accent-900/30 text-accent-700 dark:text-accent-300'
                                : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50'
                            }`}
                          >
                            {isDir ? (
                              <Folder className="w-4 h-4 text-amber-500 dark:text-amber-400 flex-shrink-0" />
                            ) : (
                              <File className="w-4 h-4 text-gray-400 dark:text-gray-500 flex-shrink-0" />
                            )}
                            <span className="truncate">{entry.name}</span>
                          </button>
                        )
                      })}
                  </>
                )}
              </div>
            </div>

            {/* Right: File content */}
            <div className="flex-1 overflow-hidden bg-white dark:bg-gray-900">
              {!selectedFile ? (
                <div className="flex items-center justify-center h-full text-gray-400 dark:text-gray-500 text-sm">
                  ファイルを選択してください
                </div>
              ) : fileLoading ? (
                <div className="flex items-center justify-center h-full text-gray-400">
                  <Loader2 className="w-5 h-5 animate-spin" />
                </div>
              ) : fileData ? (
                <FileViewer
                  content={fileData.content}
                  truncated={fileData.truncated}
                  isBinary={fileData.is_binary}
                  path={selectedFile}
                  projectId={projectId}
                />
              ) : null}
            </div>
          </>
        ) : (
          <>
            {/* Left: Changed files */}
            <div className="w-72 flex-shrink-0 border-r border-gray-200 dark:border-gray-700 overflow-y-auto">
              {gitLoading ? (
                <div className="flex items-center justify-center py-8 text-gray-400">
                  <Loader2 className="w-4 h-4 animate-spin" />
                </div>
              ) : gitFiles.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-400 dark:text-gray-500 text-sm gap-2">
                  <GitBranch className="w-8 h-8 opacity-40" />
                  変更なし
                </div>
              ) : (
                <div className="py-1">
                  {/* "All changes" option */}
                  <button
                    onClick={() => setSelectedGitFile(null)}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-sm transition-colors ${
                      selectedGitFile === null
                        ? 'bg-accent-50 dark:bg-accent-900/30 text-accent-700 dark:text-accent-300'
                        : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50'
                    }`}
                  >
                    <GitBranch className="w-3.5 h-3.5 flex-shrink-0 text-gray-400" />
                    <span className="text-xs font-medium">すべての変更</span>
                  </button>
                  {gitFiles.map((f) => (
                    <button
                      key={f.path}
                      onClick={() => setSelectedGitFile(f.path)}
                      className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors ${
                        selectedGitFile === f.path
                          ? 'bg-accent-50 dark:bg-accent-900/30 text-accent-700 dark:text-accent-300'
                          : 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50'
                      }`}
                    >
                      <span className={`font-mono font-bold w-4 text-center flex-shrink-0 ${statusColor(f.status)}`}>
                        {statusLabel(f.status)}
                      </span>
                      <span className="font-mono truncate">{f.path}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Right: Diff viewer */}
            <div className="flex-1 overflow-hidden bg-white dark:bg-gray-900">
              {diffLoading ? (
                <div className="flex items-center justify-center h-full text-gray-400">
                  <Loader2 className="w-5 h-5 animate-spin" />
                </div>
              ) : (
                <DiffViewer diff={diffData?.diff ?? ''} />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
