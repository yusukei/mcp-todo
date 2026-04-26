/**
 * Projects domain API.
 *
 * Centralizes URL construction for /projects endpoints so callers don't
 * repeat string concatenation across pages. Returns unwrapped JSON
 * (`response.data`) so consumers don't need the axios wrapper boilerplate.
 */
import { api } from './client'
import type { Project } from '../types'

/** Phase 0.5 / API-1 — sidebar "今日の動き" counters. */
export interface ProjectStatsToday {
  in_progress: number
  awaiting_decision: number
  completed_24h: number
  decisions_pending: number
  /** ISO-8601 wallclock when the snapshot was taken. */
  as_of: string
}

export const projectsApi = {
  list: () => api.get<Project[]>('/projects').then((r) => r.data),

  get: (id: string) => api.get<Project>(`/projects/${id}`).then((r) => r.data),

  summary: (id: string) =>
    api.get(`/projects/${id}/summary`).then((r) => r.data),

  /** Phase 0.5 / API-1: drives the SidebarFull "今日の動き" section. */
  statsToday: (id: string) =>
    api.get<ProjectStatsToday>(`/projects/${id}/stats/today`).then((r) => r.data),

  create: (data: Partial<Project>) =>
    api.post<Project>('/projects', data).then((r) => r.data),

  update: (id: string, data: Partial<Project>) =>
    api.patch<Project>(`/projects/${id}`, data).then((r) => r.data),

  remove: (id: string) => api.delete(`/projects/${id}`).then((r) => r.data),

  reorder: (ids: string[]) =>
    api.post('/projects/reorder', { ids }).then((r) => r.data),
}
