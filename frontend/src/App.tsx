import React, { Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from './api/client'
import { useAuthStore } from './store/auth'
import ErrorBoundary from './components/common/ErrorBoundary'
import { useGlobalErrorHandler } from './hooks/useGlobalErrorHandler'
import Layout from './components/common/Layout'
import ToastContainer from './components/common/Toast'
import ProtectedRoute from './components/common/ProtectedRoute'
import AdminRoute from './components/common/AdminRoute'
import LoginPage from './pages/LoginPage'
import ProjectsPage from './pages/ProjectsPage'
import ProjectPage from './pages/ProjectPage'
import ProjectSettingsPage from './pages/ProjectSettingsPage'
import DocumentPage from './pages/DocumentPage'
import KnowledgePage from './pages/KnowledgePage'
import DocSitesPage from './pages/DocSitesPage'
import DocSiteViewerPage from './pages/DocSiteViewerPage'
import SettingsPage from './pages/SettingsPage'

const GoogleCallbackPage = React.lazy(() => import('./pages/GoogleCallbackPage'))
const AdminPage = React.lazy(() => import('./pages/AdminPage'))
const NotFoundPage = React.lazy(() => import('./pages/NotFoundPage'))

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

const LoadingFallback = () => (
  <div className="flex items-center justify-center h-screen text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900" role="status" aria-live="polite">読み込み中...</div>
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

function AppRoutes() {
  const location = useLocation()
  useGlobalErrorHandler()
  return (
    <ErrorBoundary key={location.pathname}>
      <AppInit>
        <ToastContainer />
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
            <Route path="projects/:projectId/settings" element={<ProjectSettingsPage />} />
            <Route path="projects/:projectId/documents/:documentId" element={<DocumentPage />} />
<Route path="knowledge" element={<KnowledgePage />} />
            <Route path="knowledge/:knowledgeId" element={<KnowledgePage />} />
            <Route path="docsites" element={<DocSitesPage />} />
            <Route path="docsites/:siteId/*" element={<DocSiteViewerPage />} />
            <Route path="settings" element={<SettingsPage />} />
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
          <Route path="*" element={
            <Suspense fallback={<LoadingFallback />}>
              <NotFoundPage />
            </Suspense>
          } />
        </Routes>
      </AppInit>
    </ErrorBoundary>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
