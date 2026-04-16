/** Error tracker (Sentry-compatible) domain API. */
import { api } from './client'

export interface UserSupplied {
  _user_supplied: true
  value: string
}

export interface ErrorIssue {
  id: string
  project_id: string
  error_project_id: string
  fingerprint: string
  title: UserSupplied
  culprit: UserSupplied
  level: string
  status: 'unresolved' | 'resolved' | 'ignored'
  first_seen: string
  last_seen: string
  event_count: number
  user_count: number
  release: string | null
  environment: string | null
  assignee_id: string | null
  linked_task_ids: string[]
  tags: Record<string, string>
}

export interface ErrorProjectSummary {
  id: string
  project_id: string
  name: string
  allowed_origins: string[]
  allowed_origin_wildcard: boolean
  rate_limit_per_min: number
  retention_days: number
  scrub_ip: boolean
  auto_create_task_on_new_issue: boolean
  enabled: boolean
  keys: {
    public_key: string
    secret_key_prefix: string
    expire_at: string | null
    created_at: string
  }[]
}

export interface EventFrame {
  filename?: string
  function?: string
  lineno?: number
  colno?: number
  in_app?: boolean
  module?: string
  context_line?: string
  pre_context?: string[]
  post_context?: string[]
}

export interface EventBreadcrumb {
  timestamp: string
  category?: string
  message?: string
  level?: string
  type?: string
  data?: Record<string, unknown>
}

export interface ErrorEvent {
  id: string
  event_id?: string
  issue_id: string
  project_id: string
  fingerprint: string
  received_at: string
  timestamp: string
  platform: string
  level: string
  message: UserSupplied | null
  exception: {
    values?: {
      type?: string
      value?: string
      stacktrace?: { frames?: EventFrame[] }
    }[]
  } | null
  breadcrumbs: { values?: EventBreadcrumb[] } | null
  request: {
    method?: string
    url?: string
    query_string?: string
  } | null
  user: Record<string, unknown> | null
  tags: Record<string, string>
  contexts: Record<string, unknown>
  release: string | null
  environment: string | null
  sdk?: { name?: string; version?: string } | null
  user_agent?: string | null
  symbolicated?: boolean
}

export const errorTrackerApi = {
  listProjects: () =>
    api.get<ErrorProjectSummary[]>('/error-tracker/projects').then((r) => r.data),

  updateProject: (errorProjectId: string, data: Partial<ErrorProjectSummary>) =>
    api
      .patch(`/error-tracker/projects/${errorProjectId}`, data)
      .then((r) => r.data),

  listIssues: (
    errorProjectId: string,
    params?: {
      status?: string
      environment?: string
      release?: string
      limit?: number
    },
  ) =>
    api
      .get<ErrorIssue[]>(
        `/error-tracker/projects/${errorProjectId}/issues`,
        { params },
      )
      .then((r) => r.data),

  getIssue: (issueId: string) =>
    api.get<ErrorIssue>(`/error-tracker/issues/${issueId}`).then((r) => r.data),

  listEvents: (issueId: string, limit = 20) =>
    api
      .get<ErrorEvent[]>(`/error-tracker/issues/${issueId}/events`, { params: { limit } })
      .then((r) => r.data),

  resolve: (issueId: string, resolution?: string) =>
    api
      .post<ErrorIssue>(`/error-tracker/issues/${issueId}/resolve`, { resolution })
      .then((r) => r.data),

  ignore: (issueId: string, until?: string) =>
    api
      .post<ErrorIssue>(`/error-tracker/issues/${issueId}/ignore`, { until })
      .then((r) => r.data),

  reopen: (issueId: string) =>
    api.post<ErrorIssue>(`/error-tracker/issues/${issueId}/reopen`).then((r) => r.data),
}
