/**
 * Users domain API.
 *
 * Centralizes URL construction for admin /users endpoints. Returns
 * unwrapped JSON. The list endpoint returns a paged envelope, so the
 * helper unwraps to ``items``.
 */
import { api } from './client'
import type { User } from '../types'

/** Phase 6.B-2: project membership row returned by
 *  ``GET /users/{id}/projects``. */
export interface UserProjectMembership {
  id: string
  name: string
  color: string
  /** This user's role within the project (owner/member/viewer/etc.). */
  role: string | null
  member_count: number
  created_at: string | null
}

/** Phase 6.B-3: aggregated tool-call row in
 *  ``GET /users/{id}/ai_runs``. */
export interface UserAiRunByTool {
  tool_name: string
  call_count: number
}

export interface UserAiRunsResponse {
  total_calls: number
  by_tool: UserAiRunByTool[]
  /** ISO-8601 floor of the time window. */
  since: string
  /** Window length in days (mirror of the request param). */
  days: number
}

export const usersApi = {
  /** Admin members table (paginated envelope). */
  list: (params?: { limit?: number; skip?: number }) =>
    api
      .get<{ items: User[]; total: number; limit: number; skip: number }>(
        '/users',
        { params },
      )
      .then((r) => r.data),

  /** Single user with admin extras (ai_runs_30d / projects_count). */
  get: (id: string) =>
    api.get<User>(`/users/${id}`).then((r) => r.data),

  /** Phase 6.B-2: project memberships for a single user. */
  projects: (id: string) =>
    api
      .get<UserProjectMembership[]>(`/users/${id}/projects`)
      .then((r) => r.data),

  /** Phase 6.B-3: recent MCP tool calls aggregated by tool name.
   *  ``days`` defaults to 30 server-side. */
  aiRuns: (id: string, params?: { days?: number; limit?: number }) =>
    api
      .get<UserAiRunsResponse>(`/users/${id}/ai_runs`, { params })
      .then((r) => r.data),
}
