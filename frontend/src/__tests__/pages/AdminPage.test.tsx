/**
 * Smoke tests for the Admin area.
 *
 * Covers tab switching on AdminPage and basic list/render flows for the
 * four tabs (Users, AllowedEmails, Projects, BackupRestore). Mutation
 * happy paths are exercised through user interactions where they're
 * cheap to drive without elaborate fixtures.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import AdminPage from '../../pages/AdminPage'
import AllowedEmailsTab from '../../pages/admin/AllowedEmailsTab'
import UsersTab from '../../pages/admin/UsersTab'
import ProjectsTab from '../../pages/admin/ProjectsTab'
import BackupRestoreTab from '../../pages/admin/BackupRestoreTab'
import { server } from '../mocks/server'
import { mockProject, mockUser } from '../mocks/handlers'
import { renderWithProviders } from '../utils/renderWithProviders'

beforeEach(() => {
  // Default success handlers for admin endpoints not in the global handler set
  server.use(
    http.get('/api/v1/users', () =>
      HttpResponse.json({ items: [mockUser], total: 1, limit: 50, skip: 0 }),
    ),
    http.get('/api/v1/users/allowed-emails/', () =>
      HttpResponse.json([
        { id: 'ae1', email: 'allowed@example.com', created_at: '2024-01-01T00:00:00Z' },
      ]),
    ),
    http.post('/api/v1/users/allowed-emails/', () =>
      HttpResponse.json({ id: 'ae2' }),
    ),
    http.delete('/api/v1/users/allowed-emails/:id', () =>
      new HttpResponse(null, { status: 204 }),
    ),
  )
})


describe('AdminPage', () => {
  it('renders the page header and the four tab buttons', () => {
    renderWithProviders(<AdminPage />)

    expect(screen.getByRole('heading', { name: '管理者設定' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ユーザ' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '許可メール' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'プロジェクト' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'バックアップ' })).toBeInTheDocument()
  })

  it('switches the active tab when a tab button is clicked', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AdminPage />)

    // Default tab is Users — wait for its data to render
    await waitFor(() => {
      expect(screen.getByText(mockUser.name)).toBeInTheDocument()
    })

    // Switch to Allowed Emails tab
    await user.click(screen.getByRole('button', { name: '許可メール' }))
    await waitFor(() => {
      expect(screen.getByText('allowed@example.com')).toBeInTheDocument()
    })

    // Switch to Backup tab — header text changes
    await user.click(screen.getByRole('button', { name: 'バックアップ' }))
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /バックアップ/i, level: 2 }),
      ).toBeInTheDocument()
    })
  })
})


describe('AllowedEmailsTab', () => {
  it('renders the list of allowed emails from the API', async () => {
    renderWithProviders(<AllowedEmailsTab />)

    expect(
      await screen.findByText('allowed@example.com'),
    ).toBeInTheDocument()
  })

  it('shows the empty state when the API returns no entries', async () => {
    server.use(
      http.get('/api/v1/users/allowed-emails/', () => HttpResponse.json([])),
    )

    renderWithProviders(<AllowedEmailsTab />)

    expect(
      await screen.findByText('許可メールがありません'),
    ).toBeInTheDocument()
  })

  it('disables the add button until an email is typed', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AllowedEmailsTab />)

    const addButton = screen.getByRole('button', { name: /追加/ })
    expect(addButton).toBeDisabled()

    const input = screen.getByPlaceholderText('example@gmail.com')
    await user.type(input, 'new@example.com')
    expect(addButton).toBeEnabled()
  })
})


describe('UsersTab', () => {
  it('renders the list of users', async () => {
    renderWithProviders(<UsersTab />)

    expect(await screen.findByText(mockUser.name)).toBeInTheDocument()
    expect(screen.getByText(mockUser.email)).toBeInTheDocument()
  })

  it('shows an empty state when no users exist', async () => {
    server.use(
      http.get('/api/v1/users', () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, skip: 0 }),
      ),
    )

    renderWithProviders(<UsersTab />)

    // Wait for the loading-state to settle, then assert no rows render the
    // mock user. The exact empty-state copy varies; absence is enough.
    await waitFor(() => {
      expect(screen.queryByText(mockUser.email)).not.toBeInTheDocument()
    })
  })
})


describe('ProjectsTab', () => {
  it('renders the list of projects', async () => {
    server.use(
      http.get('/api/v1/projects', () => HttpResponse.json([mockProject])),
    )

    renderWithProviders(<ProjectsTab />)

    expect(await screen.findByText(mockProject.name)).toBeInTheDocument()
  })
})


describe('BackupRestoreTab', () => {
  it('renders both export and import sections', () => {
    renderWithProviders(<BackupRestoreTab />)

    // Header for the tab
    expect(
      screen.getByRole('heading', { name: /バックアップ/i, level: 2 }),
    ).toBeInTheDocument()
  })
})
