import { http, HttpResponse } from 'msw'

export const mockUser = {
  id: 'user-id-1',
  email: 'admin@test.com',
  name: 'Admin User',
  is_admin: true,
  picture_url: null,
}

export const mockRegularUser = {
  id: 'user-id-2',
  email: 'user@test.com',
  name: 'Regular User',
  is_admin: false,
  picture_url: null,
}

export const mockTokens = {
  access_token: 'mock-access-token',
  refresh_token: 'mock-refresh-token',
  token_type: 'bearer',
}

export const mockProject = {
  id: 'project-id-1',
  name: 'Test Project',
  description: 'Test description',
  color: '#6366f1',
  status: 'active',
  members: [{ user_id: 'user-id-1', joined_at: '2024-01-01T00:00:00Z' }],
  created_by: 'user-id-1',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

export const mockTask = {
  id: 'task-id-1',
  project_id: 'project-id-1',
  title: 'Test Task',
  description: '',
  status: 'todo',
  priority: 'medium',
  due_date: null,
  assignee_id: null,
  parent_task_id: null,
  tags: [],
  comments: [],
  created_by: 'user-id-1',
  completed_at: null,
  sort_order: 0,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

/**
 * デフォルトハンドラー (正常系)
 * 各テストで server.use() を使って上書きすることで異常系をテスト可能
 */
export const handlers = [
  // Auth
  http.post('/api/v1/auth/login', () => HttpResponse.json(mockTokens)),
  http.post('/api/v1/auth/refresh', () => HttpResponse.json(mockTokens)),
  http.get('/api/v1/auth/me', () => HttpResponse.json(mockUser)),

  // Projects
  http.get('/api/v1/projects', () => HttpResponse.json([mockProject])),
  http.post('/api/v1/projects', () =>
    HttpResponse.json(mockProject, { status: 201 })
  ),
  http.get('/api/v1/projects/:projectId', () => HttpResponse.json(mockProject)),
  http.patch('/api/v1/projects/:projectId', () => HttpResponse.json(mockProject)),
  http.delete('/api/v1/projects/:projectId', () => new HttpResponse(null, { status: 204 })),

  // Tasks
  http.get('/api/v1/projects/:projectId/tasks', () =>
    HttpResponse.json([mockTask])
  ),
  http.post('/api/v1/projects/:projectId/tasks', () =>
    HttpResponse.json(mockTask, { status: 201 })
  ),
  http.get('/api/v1/projects/:projectId/tasks/:taskId', () =>
    HttpResponse.json(mockTask)
  ),
  http.patch('/api/v1/projects/:projectId/tasks/:taskId', () =>
    HttpResponse.json(mockTask)
  ),
  http.delete('/api/v1/projects/:projectId/tasks/:taskId', () =>
    new HttpResponse(null, { status: 204 })
  ),

  // Project summary
  http.get('/api/v1/projects/:projectId/summary', () =>
    HttpResponse.json({
      project_id: 'project-id-1',
      total: 3,
      by_status: { todo: 2, done: 1 },
      completion_rate: 33.3,
    })
  ),
]
