import React, { Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ErrorBoundary from './components/common/ErrorBoundary'
import { useGlobalErrorHandler } from './hooks/useGlobalErrorHandler'
import Layout from './components/common/Layout'
import AppInit from './components/common/AppInit'
import ToastContainer from './components/common/Toast'
import ConfirmDialog from './components/common/ConfirmDialog'
import ProtectedRoute from './components/common/ProtectedRoute'
import AdminRoute from './components/common/AdminRoute'
import LoginPage from './pages/LoginPage'
import ProjectsPage from './pages/ProjectsPage'
import ProjectSettingsPage from './pages/ProjectSettingsPage'
import DocumentPage from './pages/DocumentPage'
import DocSitesPage from './pages/DocSitesPage'
import SettingsPage from './pages/SettingsPage'

// Heavy pages (>15KB) — code-split to keep the initial bundle small.
// LoadingFallback inside the route element below covers the suspense boundary.
const ProjectPage = React.lazy(() => import('./pages/ProjectPage'))
const KnowledgePage = React.lazy(() => import('./pages/KnowledgePage'))
const DocSiteViewerPage = React.lazy(() => import('./pages/DocSiteViewerPage'))
const WorkspacePage = React.lazy(() => import('./pages/WorkspacePage'))
const ChatPage = React.lazy(() => import('./pages/ChatPage'))
const BookmarksPage = React.lazy(() => import('./pages/BookmarksPage'))

const GoogleCallbackPage = React.lazy(() => import('./pages/GoogleCallbackPage'))
const AdminPage = React.lazy(() => import('./pages/AdminPage'))
const NotFoundPage = React.lazy(() => import('./pages/NotFoundPage'))

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

const LoadingFallback = () => (
  <div className="flex items-center justify-center h-screen text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900" role="status" aria-live="polite">読み込み中...</div>
)

// Wrap a lazily-loaded element in Suspense — keeps the JSX below tidy.
const lazy = (node: React.ReactNode) => (
  <Suspense fallback={<LoadingFallback />}>{node}</Suspense>
)

function AppRoutes() {
  const location = useLocation()
  useGlobalErrorHandler()
  return (
    <ErrorBoundary key={location.pathname}>
      <AppInit>
        <ToastContainer />
        <ConfirmDialog />
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/auth/google/callback" element={lazy(<GoogleCallbackPage />)} />
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
            <Route path="projects/:projectId" element={lazy(<ProjectPage />)} />
            <Route path="projects/:projectId/settings" element={<ProjectSettingsPage />} />
            <Route path="projects/:projectId/documents/:documentId" element={<DocumentPage />} />
            <Route path="knowledge" element={lazy(<KnowledgePage />)} />
            <Route path="knowledge/:knowledgeId" element={lazy(<KnowledgePage />)} />
            <Route path="docsites" element={<DocSitesPage />} />
            <Route path="docsites/:siteId/*" element={lazy(<DocSiteViewerPage />)} />
            <Route path="chat" element={lazy(<ChatPage />)} />
            <Route path="bookmarks" element={lazy(<BookmarksPage />)} />
            <Route path="bookmarks/:bookmarkId" element={lazy(<BookmarksPage />)} />
            <Route
              path="workspaces"
              element={
                <AdminRoute>
                  {lazy(<WorkspacePage />)}
                </AdminRoute>
              }
            />
            <Route path="settings" element={<SettingsPage />} />
            <Route
              path="admin"
              element={
                <AdminRoute>
                  {lazy(<AdminPage />)}
                </AdminRoute>
              }
            />
          </Route>
          <Route path="*" element={lazy(<NotFoundPage />)} />
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
