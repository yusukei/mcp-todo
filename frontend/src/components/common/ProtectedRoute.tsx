import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../../store/auth'

/**
 * Route guard for authenticated pages.
 *
 * Reads from the auth store only — tokens live in HttpOnly cookies and
 * are not visible to JavaScript. While `AppInit` is still calling
 * `/auth/me`, we render a loading state. Once `isInitialized` is true,
 * the user object decides whether to redirect.
 */
export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const isInitialized = useAuthStore((s) => s.isInitialized)

  if (!isInitialized) {
    return (
      <div className="flex items-center justify-center h-screen text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900">
        読み込み中...
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}
