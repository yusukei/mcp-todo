import { useState } from 'react'
import { Users, Mail, FolderOpen, HardDrive, Activity } from 'lucide-react'
import UsersTab from './admin/UsersTab'
import AllowedEmailsTab from './admin/AllowedEmailsTab'
import ProjectsTab from './admin/ProjectsTab'
import BackupRestoreTab from './admin/BackupRestoreTab'
import McpUsageTab from './admin/McpUsageTab'

type Tab = 'users' | 'emails' | 'projects' | 'backup' | 'mcp-usage'

const TABS: { id: Tab; label: string; icon: React.ReactNode }[] = [
  { id: 'users', label: 'ユーザ', icon: <Users className="w-4 h-4" /> },
  { id: 'emails', label: '許可メール', icon: <Mail className="w-4 h-4" /> },
  { id: 'projects', label: 'プロジェクト', icon: <FolderOpen className="w-4 h-4" /> },
  { id: 'backup', label: 'バックアップ', icon: <HardDrive className="w-4 h-4" /> },
  { id: 'mcp-usage', label: 'MCP 使用状況', icon: <Activity className="w-4 h-4" /> },
]

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>('users')

  return (
    <div className="flex flex-col h-full">
      <div className="px-8 py-4 border-b border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800">
        <h1 className="text-xl font-serif font-medium text-gray-800 dark:text-gray-100">管理者設定</h1>
      </div>
      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-4xl mx-auto">
          <div className="flex gap-1 mb-6 border-b border-gray-200 dark:border-gray-700 overflow-x-auto overflow-y-hidden">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap ${
                  tab === t.id
                    ? 'border-terracotta-600 dark:border-terracotta-400 text-terracotta-600 dark:text-terracotta-400'
                    : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
                }`}
              >
                {t.icon}{t.label}
              </button>
            ))}
          </div>
          {tab === 'users' && <UsersTab />}
          {tab === 'emails' && <AllowedEmailsTab />}
          {tab === 'projects' && <ProjectsTab />}
          {tab === 'backup' && <BackupRestoreTab />}
          {tab === 'mcp-usage' && <McpUsageTab />}
        </div>
      </div>
    </div>
  )
}
