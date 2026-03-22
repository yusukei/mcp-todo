import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import ProtectedRoute from '../../components/common/ProtectedRoute'
import { useAuthStore } from '../../store/auth'

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
    useAuthStore.setState({ user: null })
    localStorage.clear()
  })

  it('user と token が両方ない場合 /login にリダイレクト', () => {
    renderWithRouter(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    )
    expect(screen.getByText('Login Page')).toBeInTheDocument()
    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument()
  })

  it('user が存在する場合 children を描画', () => {
    useAuthStore.setState({
      user: { id: '1', email: 'a@test.com', name: 'A', is_admin: false },
    })

    renderWithRouter(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    )
    expect(screen.getByText('Protected Content')).toBeInTheDocument()
  })

  it('token だけある場合 (user=null) は children を描画 (初回ロード中)', () => {
    localStorage.setItem('access_token', 'some-token')

    renderWithRouter(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    )
    expect(screen.getByText('Protected Content')).toBeInTheDocument()
  })

  it('user はあるが token がない場合も children を描画', () => {
    useAuthStore.setState({
      user: { id: '1', email: 'a@test.com', name: 'A', is_admin: false },
    })

    renderWithRouter(
      <ProtectedRoute>
        <div>Protected Content</div>
      </ProtectedRoute>
    )
    expect(screen.getByText('Protected Content')).toBeInTheDocument()
  })
})
