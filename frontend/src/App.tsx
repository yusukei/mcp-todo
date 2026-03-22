import React, { Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from './api/client'
import { useAuthStore } from './store/auth'
import Layout from './components/common/Layout'
import ProtectedRoute from './components/common/ProtectedRoute'
import AdminRoute from './components/common/AdminRoute'
import LoginPage from './pages/LoginPage'
import ProjectsPage from './pages/ProjectsPage'
import ProjectPage from './pages/ProjectPage'

const GoogleCallbackPage = React.lazy(() => import('./pages/GoogleCallbackPage'))
const AdminPage = React.lazy(() => import('./pages/AdminPage'))

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

const LoadingFallback = () => (
  <div className="flex items-center justify-center h-screen text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900">読み込み中...</div>
)

function AppInit({ children }: { children: React.ReactNode }) {
  const setUser = useAuthStore((s) => s.setUser)
  const setInitialized = useAuthStore((s) => s.setInitialized)

  useEffect(() => {
    const token = localStorage.getItem('access_token')
    if (token) {
      api.get('/auth/me')
        .then((r) => setUser(r.data))
        .catch(() => {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
        })
        .finally(() => setInitialized(true))
    } else {
      setInitialized(true)
    }
  }, [setUser, setInitialized])

  return <>{children}</>
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppInit>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/auth/google/callback" element={
              <Suspense fallback={<LoadingFallback />}>
                <GoogleCallbackPage />
              </Suspense>
            } />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Layout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Navigate to="/projects" replace />} />
              <Route path="projects" element={<ProjectsPage />} />
              <Route path="projects/:projectId" element={<ProjectPage />} />
              <Route
                path="admin"
                element={
                  <AdminRoute>
                    <Suspense fallback={<LoadingFallback />}>
                      <AdminPage />
                    </Suspense>
                  </AdminRoute>
                }
              />
            </Route>
          </Routes>
        </AppInit>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
