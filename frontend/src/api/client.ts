import axios from 'axios'
import { useAuthStore } from '../store/auth'

export const api = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

let refreshPromise: Promise<string> | null = null

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401) {
      const refresh = localStorage.getItem('refresh_token')
      if (refresh && !error.config._retried) {
        error.config._retried = true
        if (!refreshPromise) {
          refreshPromise = (async () => {
            try {
              const { data } = await axios.post('/api/v1/auth/refresh', { refresh_token: refresh })
              localStorage.setItem('access_token', data.access_token)
              localStorage.setItem('refresh_token', data.refresh_token)
              return data.access_token
            } catch {
              useAuthStore.getState().logout()
              throw new Error('Refresh failed')
            } finally {
              refreshPromise = null
            }
          })()
        }
        const newToken = await refreshPromise
        error.config.headers.Authorization = `Bearer ${newToken}`
        return api.request(error.config)
      }
    }
    return Promise.reject(error)
  }
)
