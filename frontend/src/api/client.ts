import axios from 'axios'
import { useAuthStore } from '../store/auth'

/**
 * Axios instance for the REST API.
 *
 * Authentication is handled via HttpOnly cookies set by the backend
 * (`access_token` + `refresh_token`). The frontend never reads or stores
 * tokens directly — `withCredentials: true` lets the browser ship the
 * cookies on every request, and the response interceptor reissues them
 * via `/auth/refresh` when the access token expires.
 */
export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true,
})

let refreshPromise: Promise<void> | null = null

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401 && !error.config._retried) {
      error.config._retried = true

      // Coalesce concurrent refresh attempts so a burst of 401s only
      // triggers one /auth/refresh round-trip.
      if (!refreshPromise) {
        refreshPromise = (async () => {
          try {
            await axios.post(
              '/api/v1/auth/refresh',
              {},
              { withCredentials: true },
            )
          } catch {
            useAuthStore.getState().logout()
            throw new Error('Refresh failed')
          } finally {
            refreshPromise = null
          }
        })()
      }

      try {
        await refreshPromise
      } catch {
        return Promise.reject(error)
      }

      // Cookie has been refreshed; retry the original request.
      return api.request(error.config)
    }
    return Promise.reject(error)
  },
)
