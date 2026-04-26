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

export type ActorType = 'human' | 'ai' | 'system'

export interface Task {
  id: string
  project_id: string
  title: string
  description: string | null
  status: TaskStatus
  priority: TaskPriority
  due_date: string | null
  assignee_id: string | null
  /** Phase 0.5 / API-2: filled by ``list_tasks`` batch enrichment.
   *  ``null`` when the response came from a single-task endpoint. */
  assignee_name?: string | null
  parent_task_id: string | null
  blocks: string[]
  blocked_by: string[]
  /** Phase 0.5: cheap derived counter (``len(blocked_by)``). */
  blocked_by_count?: number
  /** Phase 0.5 / API-2: open subtask count, batch-fetched. */
  subtask_count?: number | null
  task_type: TaskType
  /** Phase 0.5: user responsible for resolving a decision-type task. */
  decider_id?: string | null
  decider_name?: string | null
  decision_requested_at?: string | null
  decision_context: DecisionContext | null
  tags: string[]
  comments: Comment[]
  attachments: Attachment[]
  is_deleted: boolean
  archived: boolean
  active_form: string | null
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
  /** Phase 0.5 / API-3: open task count for sidebar badges.
   *  Undefined on legacy responses from single-project endpoints. */
  task_count?: number
}

export type UserStatus = 'active' | 'invited' | 'suspended'

export interface User {
  id: string
  email: string
  name: string
  auth_type: 'admin' | 'google'
  is_active: boolean
  /** Phase 0.5: lifecycle status. ``is_active`` is retained for
   *  compatibility but new code should branch on ``status``. */
  status?: UserStatus
  is_admin: boolean
  picture_url?: string
  has_passkeys?: boolean
  password_disabled?: boolean
  /** Phase 0.5 / API-4: last authenticated request timestamp. */
  last_active_at?: string | null
  /** Phase 0.5 / API-4 (admin only): MCP tool call count over the
   *  last 30 days. */
  ai_runs_30d?: number
  /** Phase 0.5 / API-4 (admin only): number of projects the user
   *  is a member of. */
  projects_count?: number
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

// ── Bookmark ───────────────────────────────────────────

export type ClipStatus = 'pending' | 'processing' | 'done' | 'failed'

export interface BookmarkMetadata {
  meta_title: string
  meta_description: string
  favicon_url: string
  og_image_url: string
  site_name: string
  author: string
  published_date: string | null
}

export interface Bookmark {
  id: string
  project_id: string
  url: string
  title: string
  description: string
  tags: string[]
  collection_id: string | null
  metadata: BookmarkMetadata
  clip_status: ClipStatus
  clip_content?: string
  clip_error: string
  thumbnail_path: string
  is_starred: boolean
  sort_order: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface BookmarkCollection {
  id: string
  project_id: string
  name: string
  description: string
  icon: string
  color: string
  sort_order: number
  created_by: string
  created_at: string
  updated_at: string
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
