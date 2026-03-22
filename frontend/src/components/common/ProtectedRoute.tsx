import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../../store/auth'

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const token = localStorage.getItem('access_token')

  if (!user && !token) return <Navigate to="/login" replace />
  return <>{children}</>
}
