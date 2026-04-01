export type TaskStatus = 'todo' | 'in_progress' | 'on_hold' | 'done' | 'cancelled'
export type TaskPriority = 'low' | 'medium' | 'high' | 'urgent'
export type TaskType = 'action' | 'decision'

export interface DecisionOption {
  label: string
  description: string
}

export interface DecisionContext {
  background: string
  decision_point: string
  options: DecisionOption[]
  recommendation: string | null
}

export interface Attachment {
  id: string
  filename: string
  content_type: string
  size: number
  created_at: string
}

export interface Comment {
  id: string
  content: string
  author_id: string
  author_name: string
  created_at: string
}

export interface Task {
  id: string
  project_id: string
  title: string
  description: string | null
  status: TaskStatus
  priority: TaskPriority
  due_date: string | null
  assignee_id: string | null
  parent_task_id: string | null
  task_type: TaskType
  decision_context: DecisionContext | null
  tags: string[]
  comments: Comment[]
  attachments: Attachment[]
  is_deleted: boolean
  archived: boolean
  completion_report: string | null
  completed_at: string | null
  needs_detail: boolean
  approved: boolean
  created_by: string
  created_at: string
  updated_at: string
  sort_order: number
}

export type MemberRole = 'owner' | 'member'

export interface ProjectMember {
  user_id: string
  role: MemberRole
  joined_at: string
}

export interface Project {
  id: string
  name: string
  description: string | null
  color: string | null
  status: string
  is_locked: boolean
  members: ProjectMember[]
  created_by: string
  created_at: string
  updated_at: string
}

export interface User {
  id: string
  email: string
  name: string
  auth_type: 'admin' | 'google'
  is_active: boolean
  is_admin: boolean
  picture_url?: string
  has_passkeys?: boolean
  password_disabled?: boolean
  created_at: string
  updated_at?: string
}

export interface WebAuthnCredentialInfo {
  credential_id: string
  name: string
  created_at: string
}

export interface AuthTokens {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface McpApiKey {
  id: string
  name: string
  project_scopes: string[]
  is_active: boolean
  last_used_at: string | null
  created_by_name: string | null
  created_at: string
}

export interface AllowedEmail {
  id: string
  email: string
  created_by: string
  created_at: string
}

export type DocumentCategory = 'spec' | 'design' | 'api' | 'guide' | 'notes'

export interface ProjectDocument {
  id: string
  project_id: string
  title: string
  content: string
  tags: string[]
  category: DocumentCategory
  version: number
  sort_order: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface DocumentVersionSummary {
  id: string
  document_id: string
  version: number
  title: string
  changed_by: string
  task_id: string | null
  change_summary: string | null
  created_at: string
}

// ── DocSite ─────────────────────────────────────────────

export interface DocSiteSection {
  title: string
  path: string | null
  children: DocSiteSection[]
}

export interface DocSite {
  id: string
  name: string
  description: string
  source_url: string
  page_count: number
  sections?: DocSiteSection[]
  created_at: string
  updated_at: string
}

export interface DocPage {
  id: string
  site_id: string
  path: string
  title: string
  content: string
  sort_order: number
  created_at: string
}

// ── Knowledge ───────────────────────────────────────────

export type KnowledgeCategory = 'recipe' | 'reference' | 'tip' | 'troubleshooting' | 'architecture'

export interface Knowledge {
  id: string
  title: string
  content: string
  tags: string[]
  category: KnowledgeCategory
  source: string | null
  created_by: string
  created_at: string
  updated_at: string
}
