import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../../store/auth'

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const isInitialized = useAuthStore((s) => s.isInitialized)
  const token = localStorage.getItem('access_token')
  const [timedOut, setTimedOut] = useState(false)

  useEffect(() => {
    if (token && !isInitialized) {
      const timer = setTimeout(() => setTimedOut(true), 10000)
      return () => clearTimeout(timer)
    }
  }, [token, isInitialized])

  if (!user && !token) return <Navigate to="/login" replace />
  if (timedOut && !isInitialized) return <Navigate to="/login" replace />
  if (token && !isInitialized) {
    return <div className="flex items-center justify-center h-screen text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900">読み込み中...</div>
  }
  return <>{children}</>
}
