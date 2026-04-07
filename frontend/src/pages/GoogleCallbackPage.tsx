import { useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import { useAuthStore } from '../store/auth'

/**
 * Google OAuth コールバックページ。
 * Googleが /auth/google/callback?code=xxx&state=xxx にリダイレクト後、
 * このページがcode+stateをバックエンドに転送してトークンを取得する。
 */
export default function GoogleCallbackPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const setUser = useAuthStore((s) => s.setUser)
  const processed = useRef(false)

  useEffect(() => {
    // StrictModeの二重実行防止
    if (processed.current) return
    processed.current = true

    const code = searchParams.get('code')
    const state = searchParams.get('state')
    const error = searchParams.get('error')

    if (error) {
      navigate('/login?error=google_denied', { replace: true })
      return
    }

    if (!code || !state) {
      navigate('/login?error=invalid_callback', { replace: true })
      return
    }

    api
      .get('/auth/google/callback', { params: { code, state } })
      .then(async () => {
        // Cookie is set by the backend callback response.
        const { data: me } = await api.get('/auth/me')
        setUser(me)
        navigate('/', { replace: true })
      })
      .catch(() => {
        navigate('/login?error=google_failed', { replace: true })
      })
  }, [navigate, searchParams, setUser])

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
      <div className="text-center">
        <div className="w-8 h-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
        <p className="text-gray-600 dark:text-gray-400 text-sm">Googleでログイン中...</p>
      </div>
    </div>
  )
}
