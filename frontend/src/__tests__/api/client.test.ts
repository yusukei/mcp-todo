import { describe, it, expect, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { api, ApiError } from '../../api/client'
import { useAuthStore } from '../../store/auth'

/**
 * Cookie-based auth tests for the fetch-based api client.
 *
 * The frontend never reads or writes tokens from JS — they live in
 * HttpOnly cookies set by the backend. The client's 401 handler calls
 * /auth/refresh (the new cookie comes back in the response), retries
 * the original request, and drops local user state when refresh fails.
 */
describe('api client cookie refresh', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, isInitialized: false })
  })

  it('retries the original request after a successful /auth/refresh', async () => {
    let meCallCount = 0
    let refreshCallCount = 0
    server.use(
      http.get('/api/v1/auth/me', () => {
        meCallCount++
        if (meCallCount === 1) {
          return HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 })
        }
        return HttpResponse.json({ id: 'user-1', email: 'test@test.com', name: 'Test' })
      }),
      http.post('/api/v1/auth/refresh', () => {
        refreshCallCount++
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const response = await api.get('/auth/me')
    expect(response.data.email).toBe('test@test.com')
    expect(meCallCount).toBe(2)
    expect(refreshCallCount).toBe(1)
  })

  it('drops local user state when /auth/refresh itself returns 401', async () => {
    useAuthStore.setState({
      user: { id: 'u1', email: 'a@b.c', name: 'A' } as never,
    })

    let refreshCalled = false
    let logoutCalled = false
    server.use(
      http.get('/api/v1/auth/me', () =>
        HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 }),
      ),
      http.post('/api/v1/auth/refresh', () => {
        refreshCalled = true
        return HttpResponse.json({ detail: 'Invalid refresh token' }, { status: 401 })
      }),
      http.post('/api/v1/auth/logout', () => {
        logoutCalled = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    try {
      await api.get('/auth/me')
    } catch {
      // Expected — refresh failed
    }

    expect(refreshCalled).toBe(true)
    expect(logoutCalled).toBe(false)
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('does not call /auth/refresh when /auth/logout returns 401', async () => {
    let refreshCalled = false
    server.use(
      http.post('/api/v1/auth/logout', () =>
        HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 }),
      ),
      http.post('/api/v1/auth/refresh', () => {
        refreshCalled = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    try {
      await api.post('/auth/logout')
    } catch {
      // Expected — 401 propagates instead of triggering refresh.
    }

    expect(refreshCalled).toBe(false)
  })

  it('throws ApiError with response property on non-2xx', async () => {
    server.use(
      http.get('/api/v1/test/not-found', () =>
        HttpResponse.json({ detail: 'Not found' }, { status: 404 }),
      ),
    )

    try {
      await api.get('/test/not-found')
      expect.unreachable()
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError)
      const apiErr = err as ApiError
      expect(apiErr.status).toBe(404)
      expect(apiErr.response.status).toBe(404)
    }
  })
})
