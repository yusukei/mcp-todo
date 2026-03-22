export type TaskStatus = 'todo' | 'in_progress' | 'in_review' | 'done' | 'cancelled'
export type TaskPriority = 'low' | 'medium' | 'high' | 'urgent'

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
  tags: string[]
  comments: Comment[]
  is_deleted: boolean
  archived: boolean
  completed_at: string | null
  needs_detail: boolean
  approved: boolean
  created_by: string
  created_at: string
  updated_at: string
  sort_order?: number
}

export interface ProjectMember {
  user_id: string
  joined_at: string
}

export interface Project {
  id: string
  name: string
  description: string | null
  color: string | null
  status: string
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
  created_at: string
  updated_at?: string
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
  created_at: string
}

export interface AllowedEmail {
  id: string
  email: string
  created_by: string
  created_at: string
}
