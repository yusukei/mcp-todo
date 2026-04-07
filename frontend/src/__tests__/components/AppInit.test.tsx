/**
 * Tests for AppInit — the cookie-based auth bootstrap component.
 *
 * After the localStorage→cookie migration, AppInit always issues a
 * /auth/me request. The browser ships the HttpOnly cookie automatically
 * (or doesn't), and the bootstrap branches on the response status:
 *
 *   1. /auth/me succeeds → user populated, initialized=true
 *   2. /auth/me 401     → user stays null, initialized=true
 *   3. children are rendered regardless
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import AppInit from '../../components/common/AppInit'
import { useAuthStore } from '../../store/auth'
import { server } from '../mocks/server'
import { mockUser } from '../mocks/handlers'
import { renderWithProviders } from '../utils/renderWithProviders'

function resetAuthStore() {
  useAuthStore.setState({ user: null, isInitialized: false })
}

describe('AppInit', () => {
  beforeEach(() => {
    resetAuthStore()
  })

  afterEach(() => {
    resetAuthStore()
  })

  it('populates the user when /auth/me succeeds', async () => {
    renderWithProviders(
      <AppInit>
        <div data-testid="child">child</div>
      </AppInit>,
    )

    await waitFor(() => {
      expect(useAuthStore.getState().user).toEqual(mockUser)
    })
    expect(useAuthStore.getState().isInitialized).toBe(true)
  })

  it('leaves the user null and still marks initialized when /auth/me returns 401', async () => {
    server.use(
      http.get('/api/v1/auth/me', () =>
        HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 }),
      ),
      // Catch the refresh attempt the interceptor will issue and fail it,
      // so the test resolves quickly without retrying forever.
      http.post('/api/v1/auth/refresh', () =>
        HttpResponse.json({ detail: 'Invalid' }, { status: 401 }),
      ),
      http.post('/api/v1/auth/logout', () => new HttpResponse(null, { status: 204 })),
    )

    renderWithProviders(
      <AppInit>
        <div data-testid="child">child</div>
      </AppInit>,
    )

    await waitFor(() => {
      expect(useAuthStore.getState().isInitialized).toBe(true)
    })
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('renders children regardless of init state', async () => {
    renderWithProviders(
      <AppInit>
        <div data-testid="boot-child">boot</div>
      </AppInit>,
    )

    expect(screen.getByTestId('boot-child')).toBeInTheDocument()
  })
})
