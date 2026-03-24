import { useAuthStore } from '../store/auth'
import PasskeysTab from './admin/PasskeysTab'

export default function SettingsPage() {
  const user = useAuthStore((s) => s.user)
  const isLocalUser = user?.auth_type === 'admin'

  return (
    <div className="flex flex-col h-full">
      <div className="px-8 py-4 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <h1 className="text-xl font-bold text-gray-800 dark:text-gray-100">アカウント設定</h1>
      </div>
      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-4xl mx-auto space-y-8">
          {/* Profile info */}
          <div>
            <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">プロフィール</h2>
            <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-2">
              <div className="flex items-center gap-3">
                {user?.picture_url ? (
                  <img src={user.picture_url} alt="" className="w-10 h-10 rounded-full" />
                ) : (
                  <div className="w-10 h-10 rounded-full bg-indigo-100 dark:bg-indigo-900 flex items-center justify-center text-indigo-600 dark:text-indigo-300 font-bold text-lg">
                    {user?.name?.[0]?.toUpperCase() ?? '?'}
                  </div>
                )}
                <div>
                  <p className="font-medium text-gray-800 dark:text-gray-100">{user?.name}</p>
                  <p className="text-sm text-gray-500 dark:text-gray-400">{user?.email}</p>
                </div>
              </div>
            </div>
          </div>

          {/* Passkeys (local users only) */}
          {isLocalUser && (
            <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-6">
              <PasskeysTab />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
