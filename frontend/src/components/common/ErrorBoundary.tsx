import React from 'react'

interface Props {
  children: React.ReactNode
  /** Optional custom fallback. If omitted, renders the default full-screen UI. */
  fallback?: React.ReactNode
}

interface State {
  hasError: boolean
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('ErrorBoundary caught:', error, info.componentStack)
  }

  private handleReload = () => {
    window.location.reload()
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback !== undefined) {
        return this.props.fallback
      }
      return (
        <div className="flex items-center justify-center h-screen bg-gray-50 dark:bg-gray-900">
          <div className="text-center px-6">
            <h1 className="text-2xl font-serif font-medium text-gray-800 dark:text-gray-100 mb-2">
              予期しないエラーが発生しました
            </h1>
            <p className="text-gray-500 dark:text-gray-400 mb-6">
              問題が解決しない場合は、管理者にお問い合わせください。
            </p>
            <button
              onClick={this.handleReload}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
            >
              ページを再読み込み
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}

/**
 * Compact error UI suitable for in-layout (per-page) error boundaries:
 * fills the available container, doesn't cover the sidebar/header.
 */
export function PageErrorFallback() {
  return (
    <div className="flex items-center justify-center flex-1 p-8">
      <div className="text-center max-w-md">
        <h2 className="text-xl font-serif font-medium text-gray-800 dark:text-gray-100 mb-2">
          このページの読み込み中にエラーが発生しました
        </h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
          サイドバーから別のページに移動するか、再読み込みを試してください。
        </p>
        <button
          onClick={() => window.location.reload()}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
        >
          再読み込み
        </button>
      </div>
    </div>
  )
}
