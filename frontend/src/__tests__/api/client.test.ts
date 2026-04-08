import { describe, it, expect, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { api } from '../../api/client'
import { useAuthStore } from '../../store/auth'

/**
 * Cookie-based auth tests for the axios client.
 *
 * The frontend no longer reads or writes tokens from JS — they live in
 * HttpOnly cookies set by the backend. The interceptor's job is now to
 * call /auth/refresh on a 401 (the new cookie comes back in the
 * response), retry the original request, and force a logout when
 * refresh itself fails.
 */
describe('api client cookie refresh', () => {
  beforeEach(() => {
    // Reset auth state so logout() side-effects don't leak between tests
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

  it('coalesces concurrent refreshes into a single request', async () => {
    let refreshCallCount = 0
    const meResponses: number[] = []
    const projectsResponses: number[] = []

    server.use(
      http.get('/api/v1/auth/me', () => {
        meResponses.push(1)
        if (meResponses.length === 1) {
          return HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 })
        }
        return HttpResponse.json({ id: 'user-1', email: 'test@test.com', name: 'Test' })
      }),
      http.get('/api/v1/projects', () => {
        projectsResponses.push(1)
        if (projectsResponses.length === 1) {
          return HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 })
        }
        return HttpResponse.json([{ id: 'p1', name: 'Project' }])
      }),
      http.post('/api/v1/auth/refresh', async () => {
        refreshCallCount++
        await new Promise((r) => setTimeout(r, 50))
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const [meRes, projectsRes] = await Promise.all([
      api.get('/auth/me'),
      api.get('/projects'),
    ])

    expect(meRes.data.email).toBe('test@test.com')
    expect(projectsRes.data[0].name).toBe('Project')
    expect(refreshCallCount).toBe(1)
  })

  it('drops local user state when /auth/refresh itself returns 401', async () => {
    // Seed the store so we can verify it gets cleared.
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
        // Must NOT be called: the interceptor used to call logout()
        // here, which itself went through the same interceptor and
        // produced an infinite /refresh ↔ /logout 401 loop. The
        // current implementation drops local state directly.
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
    // Regression test for the /refresh ↔ /logout ricochet loop.
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

  it('does intercept /auth/refresh-like endpoints that are not the loop paths', async () => {
    // Regression test for the prefix-vs-substring change. A
    // hypothetical endpoint named /auth/refresher MUST still be
    // refreshed-on-401 because only "/auth/refresh" + ("/" | "?" | EOL)
    // counts as the loop path.
    let refreshCalled = false
    let resourceCalls = 0
    server.use(
      http.get('/api/v1/auth/refresher', () => {
        resourceCalls++
        if (resourceCalls === 1) {
          return HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 })
        }
        return HttpResponse.json({ ok: true })
      }),
      http.post('/api/v1/auth/refresh', () => {
        refreshCalled = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const res = await api.get('/auth/refresher')
    expect(res.data).toEqual({ ok: true })
    expect(refreshCalled).toBe(true)
    expect(resourceCalls).toBe(2)
  })
})
