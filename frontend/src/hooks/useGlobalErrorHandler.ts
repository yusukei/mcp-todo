import { useEffect } from 'react'
import { showErrorToast } from '../components/common/Toast'
import { captureException } from '../lib/sentry'

/**
 * Listens for unhandled promise rejections and uncaught errors,
 * displaying them as error toasts. This complements React's ErrorBoundary
 * which only catches synchronous render errors.
 */
export function useGlobalErrorHandler() {
  useEffect(() => {
    function handleUnhandledRejection(event: PromiseRejectionEvent) {
      const reason = event.reason
      let message: string

      if (reason instanceof Error) {
        message = reason.message
      } else if (typeof reason === 'string') {
        message = reason
      } else {
        message = '予期しないエラーが発生しました'
      }

      // Skip network errors already handled by axios interceptors (401, 403, etc.)
      if (reason?.response?.status === 401 || reason?.response?.status === 403) {
        return
      }

      console.error('Unhandled promise rejection:', reason)
      captureException(reason)
      showErrorToast(message)
    }

    function handleError(event: ErrorEvent) {
      console.error('Uncaught error:', event.error)
      captureException(event.error ?? new Error(event.message))
      showErrorToast(event.message || '予期しないエラーが発生しました')
    }

    window.addEventListener('unhandledrejection', handleUnhandledRejection)
    window.addEventListener('error', handleError)

    return () => {
      window.removeEventListener('unhandledrejection', handleUnhandledRejection)
      window.removeEventListener('error', handleError)
    }
  }, [])
}
