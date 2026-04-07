import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import LoginPage from '../../pages/LoginPage'
import { useAuthStore } from '../../store/auth'
import { server } from '../mocks/server'
import { mockTokens, mockUser } from '../mocks/handlers'

// navigate のモック
const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

function renderLoginPage() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    mockNavigate.mockClear()
    useAuthStore.setState({ user: null })
  })

  it('フォームが描画される', () => {
    renderLoginPage()
    expect(screen.getByLabelText('メールアドレス')).toBeInTheDocument()
    expect(screen.getByLabelText('パスワード')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ログイン' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Google でログイン/ })).toBeInTheDocument()
  })

  it('ログイン成功後に / へナビゲートする', async () => {
    renderLoginPage()
    await userEvent.type(screen.getByLabelText('メールアドレス'), 'admin@test.com')
    await userEvent.type(screen.getByLabelText('パスワード'), 'password')
    await userEvent.click(screen.getByRole('button', { name: 'ログイン' }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/')
    })
  })

  it('ログイン成功後に Zustand store にユーザー情報が設定される', async () => {
    renderLoginPage()
    await userEvent.type(screen.getByLabelText('メールアドレス'), 'admin@test.com')
    await userEvent.type(screen.getByLabelText('パスワード'), 'password')
    await userEvent.click(screen.getByRole('button', { name: 'ログイン' }))

    await waitFor(() => {
      const { user } = useAuthStore.getState()
      expect(user?.email).toBe(mockUser.email)
    })
  })

  it('ログイン失敗時にエラーメッセージを表示する', async () => {
    server.use(
      http.post('/api/v1/auth/login', () =>
        HttpResponse.json({ detail: 'Invalid credentials' }, { status: 401 })
      )
    )

    renderLoginPage()
    await userEvent.type(screen.getByLabelText('メールアドレス'), 'bad@test.com')
    await userEvent.type(screen.getByLabelText('パスワード'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'ログイン' }))

    await waitFor(() => {
      expect(
        screen.getByText('ユーザ名またはパスワードが正しくありません')
      ).toBeInTheDocument()
    })
  })

  it('ログイン中はボタンが disabled で "ログイン中..." と表示される', async () => {
    // レスポンスを遅延させてローディング状態を観察
    server.use(
      http.post('/api/v1/auth/login', async () => {
        await new Promise((r) => setTimeout(r, 100))
        return HttpResponse.json(mockTokens)
      })
    )

    renderLoginPage()
    await userEvent.type(screen.getByLabelText('メールアドレス'), 'admin@test.com')
    await userEvent.type(screen.getByLabelText('パスワード'), 'pass')
    await userEvent.click(screen.getByRole('button', { name: 'ログイン' }))

    expect(screen.getByRole('button', { name: 'ログイン中...' })).toBeDisabled()

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'ログイン中...' })).not.toBeInTheDocument()
    })
  })

  it('"Google でログイン" クリックで window.location.href が変更される', async () => {
    const hrefSpy = vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...window.location,
      href: '',
    } as Location)

    // Object.defineProperty でセッターをモック
    let capturedHref = ''
    Object.defineProperty(window, 'location', {
      value: {
        ...window.location,
        set href(val: string) {
          capturedHref = val
        },
        get href() {
          return capturedHref
        },
      },
      configurable: true,
    })

    renderLoginPage()
    await userEvent.click(screen.getByRole('button', { name: /Google でログイン/ }))

    expect(capturedHref).toBe('/api/v1/auth/google')
    hrefSpy.mockRestore()
  })
})
