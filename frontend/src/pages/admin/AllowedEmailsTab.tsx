import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Plus } from 'lucide-react'
import { api } from '../../api/client'
import { showErrorToast } from '../../components/common/Toast'
import type { AllowedEmail } from '../../types'

export default function AllowedEmailsTab() {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')

  const { data: entries = [] } = useQuery({
    queryKey: ['admin-allowed-emails'],
    queryFn: () => api.get('/users/allowed-emails/').then((r) => r.data),
  })

  const add = useMutation({
    mutationFn: () => api.post('/users/allowed-emails/', { email }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-allowed-emails'] }); setEmail('') },
    onError: () => showErrorToast('許可メールの追加に失敗しました'),
  })

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/users/allowed-emails/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-allowed-emails'] }),
    onError: () => showErrorToast('許可メールの削除に失敗しました'),
  })

  return (
    <div>
      <h2 className="text-base font-semibold text-gray-700 dark:text-gray-200 mb-4">Google OAuth 許可メール</h2>
      <div className="flex gap-2 mb-4">
        <input
          placeholder="example@gmail.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && email && add.mutate()}
          className="flex-1 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <button
          onClick={() => add.mutate()}
          disabled={!email || add.isPending}
          className="flex items-center gap-1.5 px-3 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
        >
          <Plus className="w-4 h-4" />追加
        </button>
      </div>
      <div className="border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-700 text-gray-500 dark:text-gray-400 text-xs uppercase">
            <tr>
              <th className="px-4 py-3 text-left">メールアドレス</th>
              <th className="px-4 py-3 text-left">登録日</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {entries.map((e: AllowedEmail) => (
              <tr key={e.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td className="px-4 py-3 text-gray-700 dark:text-gray-300">{e.email}</td>
                <td className="px-4 py-3 text-gray-400 dark:text-gray-500">{new Date(e.created_at).toLocaleDateString('ja-JP')}</td>
                <td className="px-4 py-3 text-right">
                  <button onClick={() => del.mutate(e.id)} className="text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400" aria-label="削除">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr><td colSpan={3} className="px-4 py-8 text-center text-gray-400 dark:text-gray-500">許可メールがありません</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
