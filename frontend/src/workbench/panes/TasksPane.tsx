import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ExternalLink, Loader2 } from 'lucide-react'
import { api } from '../../api/client'
import type { Task } from '../../types'
import { STATUS_LABELS, STATUS_COLORS } from '../../constants/task'
import type { PaneComponentProps } from '../paneRegistry'

const VIEW_MODES = ['list', 'board', 'timeline'] as const
type ViewMode = (typeof VIEW_MODES)[number]

const isViewMode = (v: unknown): v is ViewMode =>
  typeof v === 'string' && (VIEW_MODES as readonly string[]).includes(v)

const lastViewKey = (projectId: string) => `lastView:${projectId}`

/**
 * Initial Tasks pane (PR1 scope). Renders a compact, read-only list
 * of the project's tasks grouped by status. Per-pane state seeds its
 * ``viewMode`` from the legacy ``localStorage["lastView:{projectId}"]``
 * value the existing ProjectPage uses, then persists subsequent
 * changes to ``paneConfig.viewMode`` so two Tasks tabs in the same
 * Workbench can show different views.
 *
 * The actual Board / Timeline views land in PR4 polish; for now any
 * non-list mode falls back to the list rendering with a hint. Goal:
 * verify the layout + persistence foundation end-to-end without
 * dragging the entire ProjectPage data layer into Phase C PR1.
 */
export default function TasksPane({
  projectId,
  paneConfig,
  onConfigChange,
}: PaneComponentProps) {
  const persistedView = paneConfig.viewMode
  const viewMode: ViewMode = isViewMode(persistedView)
    ? persistedView
    : 'list'

  // Seed paneConfig.viewMode from the legacy key on first mount so
  // the user's existing preference flows through. After this the
  // pane owns the viewMode and the legacy key is ignored for *this*
  // pane (other tabs / standalone ProjectPage continue to use it).
  useEffect(() => {
    if (paneConfig.viewMode) return
    try {
      const legacy = window.localStorage.getItem(lastViewKey(projectId))
      if (isViewMode(legacy)) {
        onConfigChange({ viewMode: legacy })
      }
    } catch {
      /* ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const { data: tasks = [], isLoading, isError } = useQuery<Task[]>({
    queryKey: ['tasks', projectId, 'workbench'],
    queryFn: () =>
      api
        .get(`/projects/${projectId}/tasks`, {
          params: { archived: false },
        })
        // The list endpoint returns ``{items, total, ...}`` (paged
        // shape). Extract ``items`` so the rest of this component
        // can treat ``tasks`` as a plain array.
        .then((r) => (r.data?.items ?? []) as Task[]),
    enabled: !!projectId,
  })

  if (isLoading) {
    return (
      <div className="h-full flex items-center justify-center text-gray-500">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    )
  }
  if (isError) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-red-500">
        Failed to load tasks for this project.
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
        <ViewModeSwitch
          mode={viewMode}
          onChange={(m) => onConfigChange({ viewMode: m })}
        />
        <Link
          to={`/projects/${projectId}`}
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
          title="Open the full project page (Board / Timeline / filters)"
        >
          <ExternalLink className="w-3 h-3" />
          Open project page
        </Link>
      </div>

      <div className="flex-1 overflow-auto">
        {tasks.length === 0 ? (
          <p className="p-6 text-sm text-gray-500 text-center">
            No tasks in this project yet.
          </p>
        ) : (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {tasks
              .filter((t) => !t.archived)
              .map((task) => (
                <li
                  key={task.id}
                  className="px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800/40"
                >
                  <div className="flex items-start gap-2">
                    <span
                      className={`mt-0.5 inline-flex items-center px-1.5 py-0.5 text-[10px] rounded ${
                        STATUS_COLORS[task.status] ??
                        'bg-gray-200 text-gray-700'
                      }`}
                    >
                      {STATUS_LABELS[task.status] ?? task.status}
                    </span>
                    <Link
                      to={`/projects/${projectId}?task=${task.id}`}
                      className="flex-1 text-sm text-gray-800 dark:text-gray-200 hover:text-blue-600 dark:hover:text-blue-400"
                    >
                      {task.title}
                    </Link>
                    {task.priority && (
                      <span className="text-xs text-gray-400">
                        {task.priority}
                      </span>
                    )}
                  </div>
                  {task.active_form && task.status === 'in_progress' && (
                    <p className="ml-14 text-xs text-blue-600 dark:text-blue-400 italic mt-0.5">
                      {task.active_form}
                    </p>
                  )}
                </li>
              ))}
          </ul>
        )}
      </div>

      {viewMode !== 'list' && (
        <p className="px-3 py-1.5 text-[10px] text-amber-600 dark:text-amber-400 border-t border-gray-200 dark:border-gray-700">
          {viewMode === 'board' ? 'Board' : 'Timeline'} view ships in
          a follow-up; currently rendered as list.
        </p>
      )}
    </div>
  )
}

interface ViewModeSwitchProps {
  mode: ViewMode
  onChange: (m: ViewMode) => void
}

function ViewModeSwitch({ mode, onChange }: ViewModeSwitchProps) {
  return (
    <div className="flex items-center gap-1 text-xs">
      {VIEW_MODES.map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onChange(m)}
          className={`px-2 py-0.5 rounded ${
            m === mode
              ? 'bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100'
              : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
          }`}
        >
          {m[0].toUpperCase() + m.slice(1)}
        </button>
      ))}
    </div>
  )
}
