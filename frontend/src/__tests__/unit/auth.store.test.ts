import { describe, it, expect, beforeEach } from 'vitest'
import { useAuthStore } from '../../store/auth'

const mockUser = {
  id: 'user-1',
  email: 'test@example.com',
  name: 'Test User',
  is_admin: false,
}

describe('useAuthStore', () => {
  beforeEach(() => {
    // ストアを初期状態にリセット
    useAuthStore.setState({ user: null })
    localStorage.clear()
  })

  it('初期状態で user は null', () => {
    const { user } = useAuthStore.getState()
    expect(user).toBeNull()
  })

  it('setUser でユーザー情報が反映される', () => {
    useAuthStore.getState().setUser(mockUser)
    expect(useAuthStore.getState().user).toEqual(mockUser)
  })

  it('setUser(null) で user が null になる', () => {
    useAuthStore.getState().setUser(mockUser)
    useAuthStore.getState().setUser(null)
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('logout で user が null になる', () => {
    useAuthStore.getState().setUser(mockUser)
    useAuthStore.getState().logout()
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('logout で localStorage の access_token が削除される', () => {
    localStorage.setItem('access_token', 'my-token')
    useAuthStore.getState().logout()
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('logout で localStorage の refresh_token が削除される', () => {
    localStorage.setItem('refresh_token', 'my-refresh')
    useAuthStore.getState().logout()
    expect(localStorage.getItem('refresh_token')).toBeNull()
  })

  it('logout 後も他の localStorage キーは残る', () => {
    localStorage.setItem('other_key', 'other_value')
    localStorage.setItem('access_token', 'token')
    useAuthStore.getState().logout()
    expect(localStorage.getItem('other_key')).toBe('other_value')
  })
})
