/**
 * Tests for AppInit — the auth bootstrap component.
 *
 * Covers the three branches of the boot sequence:
 *   1. token present + /auth/me succeeds → user populated, initialized=true
 *   2. token present + /auth/me fails    → tokens cleared, initialized=true
 *   3. no token                           → no API call, initialized=true
 *
 * Uses MSW for the /auth/me handler and renderWithProviders for the
 * QueryClient + Router wrapping (the component itself doesn't use the
 * router, but the helper sets up a clean QueryClient per test).
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
    localStorage.clear()
    resetAuthStore()
  })

  afterEach(() => {
    localStorage.clear()
    resetAuthStore()
  })

  it('marks initialized=true immediately when no token is present', async () => {
    renderWithProviders(
      <AppInit>
        <div data-testid="child">child</div>
      </AppInit>,
    )

    expect(screen.getByTestId('child')).toBeInTheDocument()

    await waitFor(() => {
      expect(useAuthStore.getState().isInitialized).toBe(true)
    })
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('fetches /auth/me and populates the user when a token is present', async () => {
    localStorage.setItem('access_token', 'test-token')

    renderWithProviders(
      <AppInit>
        <div data-testid="child">child</div>
      </AppInit>,
    )

    await waitFor(() => {
      expect(useAuthStore.getState().user).toEqual(mockUser)
    })
    expect(useAuthStore.getState().isInitialized).toBe(true)
    // Token should NOT be cleared on success
    expect(localStorage.getItem('access_token')).toBe('test-token')
  })

  it('clears stale tokens when /auth/me fails', async () => {
    localStorage.setItem('access_token', 'expired-token')
    localStorage.setItem('refresh_token', 'expired-refresh')

    server.use(
      http.get('/api/v1/auth/me', () =>
        HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 }),
      ),
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
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
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
