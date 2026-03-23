import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { api } from '../../api/client'
import { showErrorToast } from '../../components/common/Toast'
import type { Project } from '../../types'

export default function ProjectsTab() {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [color, setColor] = useState('#6366f1')

  const { data: projects = [] } = useQuery({
    queryKey: ['admin-projects'],
    queryFn: () => api.get('/projects').then((r) => r.data),
  })

  const create = useMutation({
    mutationFn: () => api.post('/projects', { name, description, color }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-projects'] })
      qc.invalidateQueries({ queryKey: ['projects'] })
      setName(''); setDescription(''); setColor('#6366f1'); setShowForm(false)
    },
    onError: () => showErrorToast('プロジェクトの作成に失敗しました'),
  })

  const archive = useMutation({
    mutationFn: (id: string) => api.patch(`/projects/${id}`, { status: 'archived' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-projects'] })
      qc.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: () => showErrorToast('プロジェクトのアーカイブに失敗しました'),
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-gray-700 dark:text-gray-200">プロジェクト管理</h2>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
        >
          <Plus className="w-4 h-4" />プロジェクト追加
        </button>
      </div>

      {showForm && (
        <div className="mb-4 p-4 border border-gray-200 dark:border-gray-600 rounded-xl bg-gray-50 dark:bg-gray-700 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <input
              placeholder="プロジェクト名"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <div className="flex items-center gap-2">
              <label className="text-sm text-gray-600 dark:text-gray-300">カラー</label>
              <input
                type="color"
                value={color}
                onChange={(e) => setColor(e.target.value)}
                className="w-8 h-8 rounded cursor-pointer border-0"
              />
              <span className="text-xs text-gray-400 dark:text-gray-500">{color}</span>
            </div>
            <input
              placeholder="説明（任意）"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="col-span-2 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowForm(false)} className="px-3 py-1.5 text-sm text-gray-600 dark:text-gray-300 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-600">キャンセル</button>
            <button
              onClick={() => create.mutate()}
              disabled={!name || create.isPending}
              className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {create.isPending ? '作成中...' : '作成'}
            </button>
          </div>
        </div>
      )}

      <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400 text-xs uppercase">
            <tr>
              <th className="px-4 py-3 text-left">プロジェクト</th>
              <th className="px-4 py-3 text-left">説明</th>
              <th className="px-4 py-3 text-center">メンバー</th>
              <th className="px-4 py-3 text-center">ステータス</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {projects.map((p: Project) => (
              <tr key={p.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: p.color ?? undefined }} />
                    <span className="font-medium text-gray-800 dark:text-gray-200">{p.name}</span>
                  </div>
                </td>
                <td className="px-4 py-3 text-gray-500 dark:text-gray-400 truncate max-w-xs">{p.description || '—'}</td>
                <td className="px-4 py-3 text-center text-gray-500 dark:text-gray-400">{p.members?.length ?? 0}</td>
                <td className="px-4 py-3 text-center">
                  <span className={`px-2 py-0.5 text-xs rounded-full font-medium ${p.status === 'active' ? 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-400' : 'bg-gray-100 dark:bg-gray-600 text-gray-500 dark:text-gray-400'}`}>
                    {p.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  {p.status === 'active' && (
                    <button
                      onClick={() => { if (confirm(`"${p.name}" をアーカイブしますか？`)) archive.mutate(p.id) }}
                      className="text-xs text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400"
                    >
                      アーカイブ
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
