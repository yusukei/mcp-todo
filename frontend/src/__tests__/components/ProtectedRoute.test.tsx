import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import ProtectedRoute from '../../components/common/ProtectedRoute'
import { useAuthStore } from '../../store/auth'
import { createMockUser } from '../mocks/factories'

function renderWithRouter(ui: React.ReactNode, initialPath = '/protected') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/protected" element={ui} />
        <Route path="/login" element={<div>Login Page</div>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, isInitialized: false })
  })

  it('未初期化のあいだは読み込み中を表示する', () => {
    useAuthStore.setState({ user: null, isInitialized: false })

    renderWithRouter(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    )

    expect(screen.getByText('読み込み中...')).toBeInTheDocument()
    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument()
  })

  it('初期化済み + user なしなら /login にリダイレクト', () => {
    useAuthStore.setState({ user: null, isInitialized: true })

    renderWithRouter(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    )

    expect(screen.getByText('Login Page')).toBeInTheDocument()
    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument()
  })

  it('初期化済み + user ありなら children を描画', () => {
    useAuthStore.setState({
      user: createMockUser({ id: '1', email: 'a@test.com', name: 'A', is_admin: false }),
      isInitialized: true,
    })

    renderWithRouter(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    )

    expect(screen.getByText('Protected Content')).toBeInTheDocument()
  })
})
