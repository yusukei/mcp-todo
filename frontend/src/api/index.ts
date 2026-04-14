/**
 * Public API barrel — domain-grouped clients on top of the shared
 * axios `api` instance. Prefer importing the typed wrappers from here
 * over calling `api.get('/...')` directly so URL strings are centralized.
 *
 *     import { projectsApi, tasksApi } from '../api'
 *     const projects = await projectsApi.list()
 *
 * The raw `api` axios instance is still re-exported for cases where the
 * domain wrappers don't yet cover an endpoint.
 */
export { api } from './client'
export { projectsApi } from './projects'
export { tasksApi } from './tasks'
export { chatApi } from './chat'
export { knowledgeApi } from './knowledge'
export { bookmarksApi, bookmarkCollectionsApi } from './bookmarks'
export { secretsApi } from './secrets'
export { errorTrackerApi } from './errorTracker'
export type { ErrorIssue, ErrorProjectSummary, UserSupplied } from './errorTracker'
