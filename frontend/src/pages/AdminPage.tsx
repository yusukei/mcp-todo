import { useState } from 'react'
import { Users, Mail, FolderOpen, HardDrive, Activity, Package, Bot } from 'lucide-react'
import UsersTab from './admin/UsersTab'
import AllowedEmailsTab from './admin/AllowedEmailsTab'
import ProjectsTab from './admin/ProjectsTab'
import BackupRestoreTab from './admin/BackupRestoreTab'
import McpUsageTab from './admin/McpUsageTab'
import BinaryManagementTab from './admin/BinaryManagementTab'
import WorkspacePage from './WorkspacePage'
import AdminHeader from './admin/AdminHeader'

type Tab = 'users' | 'emails' | 'projects' | 'backup' | 'mcp-usage' | 'agents' | 'binaries'

interface TabDef {
  id: Tab
  label: string
  icon: React.ReactNode
  // Tabs that need full-bleed layout (their own sidebar/scroll handling)
  fullBleed?: boolean
}

const TABS: TabDef[] = [
  { id: 'users', label: 'ユーザ', icon: <Users className="w-4 h-4" /> },
  { id: 'emails', label: '許可メール', icon: <Mail className="w-4 h-4" /> },
  { id: 'projects', label: 'プロジェクト', icon: <FolderOpen className="w-4 h-4" /> },
  { id: 'backup', label: 'バックアップ', icon: <HardDrive className="w-4 h-4" /> },
  { id: 'mcp-usage', label: 'MCP 使用状況', icon: <Activity className="w-4 h-4" /> },
  { id: 'agents', label: 'エージェント管理', icon: <Bot className="w-4 h-4" />, fullBleed: true },
  { id: 'binaries', label: 'バイナリ管理', icon: <Package className="w-4 h-4" /> },
]

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>('users')
  const currentTab = TABS.find((t) => t.id === tab)
  const fullBleed = currentTab?.fullBleed ?? false

  return (
    <div className="flex flex-col h-full bg-gray-900">
      <AdminHeader
        overline="ADMIN"
        title={currentTab?.label ?? '管理者設定'}
        subtitle="ユーザー / プロジェクト / バックアップ / MCP 使用状況 など、システム全体の設定"
      />
      <div className="flex-1 min-h-0 flex">
        {/* Vertical tab navigation (P1-B: Monokai に揃える) */}
        <aside className="w-52 flex-shrink-0 bg-gray-950 border-r border-line-2 overflow-y-auto">
          <nav className="flex flex-col p-2 gap-0.5">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex items-center gap-2 px-3 py-2 text-sm rounded-comfortable transition-colors text-left ${
                  tab === t.id
                    ? 'bg-gray-700 text-gray-50 font-medium'
                    : 'text-gray-100 hover:bg-gray-800'
                }`}
              >
                <span className={tab === t.id ? 'text-accent-400' : 'text-gray-300'}>
                  {t.icon}
                </span>
                <span className="truncate">{t.label}</span>
              </button>
            ))}
          </nav>
        </aside>

        {/* Tab content */}
        {fullBleed ? (
          <div className="flex-1 min-w-0 min-h-0 overflow-hidden bg-gray-900">
            {tab === 'agents' && <WorkspacePage />}
          </div>
        ) : (
          <div className="flex-1 min-w-0 min-h-0 overflow-auto bg-gray-900">
            <div className="max-w-4xl mx-auto p-8">
              {tab === 'users' && <UsersTab />}
              {tab === 'emails' && <AllowedEmailsTab />}
              {tab === 'projects' && <ProjectsTab />}
              {tab === 'backup' && <BackupRestoreTab />}
              {tab === 'mcp-usage' && <McpUsageTab />}
              {tab === 'binaries' && <BinaryManagementTab />}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
