import { describe, it, expect, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { useAuthStore } from '../../store/auth'
import { createMockUser } from '../mocks/factories'
import { server } from '../mocks/server'

const mockUser = createMockUser({
  id: 'user-1',
  email: 'test@example.com',
  name: 'Test User',
  is_admin: false,
})

describe('useAuthStore', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, isInitialized: false })
  })

  it('初期状態で user は null', () => {
    expect(useAuthStore.getState().user).toBeNull()
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

  it('setInitialized でフラグが切り替わる', () => {
    useAuthStore.getState().setInitialized(true)
    expect(useAuthStore.getState().isInitialized).toBe(true)
  })

  it('logout で user が null になる', async () => {
    server.use(
      http.post('/api/v1/auth/logout', () => new HttpResponse(null, { status: 204 })),
    )

    useAuthStore.getState().setUser(mockUser)
    await useAuthStore.getState().logout()
    expect(useAuthStore.getState().user).toBeNull()
  })

  it('logout は /auth/logout を呼び出す', async () => {
    let logoutCalled = false
    server.use(
      http.post('/api/v1/auth/logout', () => {
        logoutCalled = true
        return new HttpResponse(null, { status: 204 })
      }),
    )

    useAuthStore.getState().setUser(mockUser)
    await useAuthStore.getState().logout()
    expect(logoutCalled).toBe(true)
  })

  it('logout はサーバ側エラーでも user を null にする', async () => {
    server.use(
      http.post('/api/v1/auth/logout', () =>
        HttpResponse.json({ detail: 'server down' }, { status: 500 }),
      ),
    )

    useAuthStore.getState().setUser(mockUser)
    await useAuthStore.getState().logout()
    expect(useAuthStore.getState().user).toBeNull()
  })
})
