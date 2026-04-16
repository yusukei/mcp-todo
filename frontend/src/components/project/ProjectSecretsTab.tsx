import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Eye, EyeOff, Copy, Pencil, Trash2, KeyRound } from 'lucide-react'
import { secretsApi } from '../../api/secrets'
import { showErrorToast, showSuccessToast } from '../common/Toast'
import { showConfirm } from '../common/ConfirmDialog'
import { captureException } from '../../lib/sentry'

interface Secret {
  id: string
  project_id: string
  key: string
  description: string
  created_by: string
  updated_by: string
  created_at: string
  updated_at: string
}

interface Props {
  projectId: string
  isOwner: boolean
}

export default function ProjectSecretsTab({ projectId, isOwner }: Props) {
  const qc = useQueryClient()

  // ── List ───────────────────────────────────────────────
  const { data, isLoading } = useQuery({
    queryKey: ['secrets', projectId],
    queryFn: () => secretsApi.list(projectId),
    enabled: !!projectId,
  })
  const secrets: Secret[] = data?.items ?? []

  // ── Revealed values (auto-hide after 30s) ──────────────
  const [revealed, setRevealed] = useState<Record<string, string>>({})
  const [timers, setTimers] = useState<Record<string, ReturnType<typeof setTimeout>>>({})

  const revealValue = useCallback(async (key: string) => {
    try {
      const res = await secretsApi.getValue(projectId, key)
      setRevealed((prev) => ({ ...prev, [key]: res.value }))
      // Auto-hide after 30 seconds
      const timer = setTimeout(() => {
        setRevealed((prev) => {
          const next = { ...prev }
          delete next[key]
          return next
        })
      }, 30000)
      setTimers((prev) => {
        if (prev[key]) clearTimeout(prev[key])
        return { ...prev, [key]: timer }
      })
    } catch (err) {
      console.error('Failed to retrieve secret value:', err)
      showErrorToast('Failed to retrieve secret value')
    }
  }, [projectId])

  const hideValue = useCallback((key: string) => {
    setRevealed((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
    setTimers((prev) => {
      if (prev[key]) clearTimeout(prev[key])
      const next = { ...prev }
      delete next[key]
      return next
    })
  }, [])

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      Object.values(timers).forEach(clearTimeout)
    }
  }, [timers])

  const copyValue = useCallback(async (key: string) => {
    try {
      let val = revealed[key]
      if (!val) {
        const res = await secretsApi.getValue(projectId, key)
        val = res.value
      }
      if (!navigator.clipboard) {
        throw new Error('Clipboard API unavailable (requires HTTPS)')
      }
      await navigator.clipboard.writeText(val)
      showSuccessToast('Copied')
    } catch (err) {
      console.error('Copy failed:', err)
      captureException(err, { component: 'ProjectSecretsTab', action: 'copyValue' })
      showErrorToast('Copy failed')
    }
  }, [projectId, revealed])

  // ── Create / Edit modal state ──────────────────────────
  const [showForm, setShowForm] = useState(false)
  const [editKey, setEditKey] = useState<string | null>(null)
  const [formKey, setFormKey] = useState('')
  const [formValue, setFormValue] = useState('')
  const [formDesc, setFormDesc] = useState('')

  const openCreate = () => {
    setEditKey(null)
    setFormKey('')
    setFormValue('')
    setFormDesc('')
    setShowForm(true)
  }

  const openEdit = (s: Secret) => {
    setEditKey(s.key)
    setFormKey(s.key)
    setFormValue('')
    setFormDesc(s.description)
    setShowForm(true)
  }

  const createMutation = useMutation({
    mutationFn: (data: { key: string; value: string; description: string }) =>
      secretsApi.create(projectId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['secrets', projectId] })
      setShowForm(false)
      showSuccessToast('Secret created')
    },
    onError: (e: Error & { response?: { data?: { detail?: string } } }) =>
      showErrorToast(e.response?.data?.detail ?? 'Create failed'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ key, data }: { key: string; data: { value?: string; description?: string } }) =>
      secretsApi.update(projectId, key, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['secrets', projectId] })
      setShowForm(false)
      showSuccessToast('Secret updated')
    },
    onError: () => showErrorToast('Update failed'),
  })

  const deleteMutation = useMutation({
    mutationFn: (key: string) => secretsApi.remove(projectId, key),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['secrets', projectId] })
      showSuccessToast('Secret deleted')
    },
    onError: () => showErrorToast('Delete failed'),
  })

  const handleSubmit = () => {
    if (editKey) {
      const data: { value?: string; description?: string } = {}
      if (formValue) data.value = formValue
      if (formDesc !== undefined) data.description = formDesc
      updateMutation.mutate({ key: editKey, data })
    } else {
      if (!formKey.trim() || !formValue) {
        showErrorToast('Key and value are required')
        return
      }
      createMutation.mutate({ key: formKey.trim(), value: formValue, description: formDesc })
    }
  }

  const handleDelete = async (key: string) => {
    const ok = await showConfirm(`Delete secret "${key}"? This cannot be undone.`)
    if (ok) deleteMutation.mutate(key)
  }

  if (isLoading) {
    return <div className="text-center text-gray-500 dark:text-gray-400 py-8">Loading...</div>
  }

  return (
    <section>
      <div className="bg-gray-100 dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <KeyRound className="w-5 h-5 text-gray-400" />
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Secrets</h2>
            <span className="text-xs text-gray-400">({secrets.length})</span>
          </div>
          {isOwner && (
            <button
              onClick={openCreate}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-terracotta-500 text-gray-100 rounded-lg hover:bg-terracotta-600 transition-colors"
            >
              <Plus className="w-4 h-4" />
              Add
            </button>
          )}
        </div>

        {secrets.length === 0 ? (
          <div className="px-6 py-8 text-center text-gray-500 dark:text-gray-400 text-sm">
            No secrets configured
          </div>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-700">
            {secrets.map((s) => (
              <div key={s.id} className="px-6 py-3 flex items-center gap-3 group">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">
                      {s.key}
                    </span>
                  </div>
                  {s.description && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate">
                      {s.description}
                    </p>
                  )}
                  <div className="flex items-center gap-2 mt-1">
                    {revealed[s.key] ? (
                      <code className="text-xs bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 px-2 py-0.5 rounded font-mono max-w-xs truncate">
                        {revealed[s.key]}
                      </code>
                    ) : (
                      <span className="text-xs text-gray-400 tracking-widest">{'*'.repeat(12)}</span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
                  {revealed[s.key] ? (
                    <button
                      onClick={() => hideValue(s.key)}
                      className="p-1.5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                      title="Hide"
                    >
                      <EyeOff className="w-4 h-4" />
                    </button>
                  ) : (
                    <button
                      onClick={() => revealValue(s.key)}
                      className="p-1.5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                      title="Show"
                    >
                      <Eye className="w-4 h-4" />
                    </button>
                  )}
                  <button
                    onClick={() => copyValue(s.key)}
                    className="p-1.5 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                    title="Copy"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                  {isOwner && (
                    <>
                      <button
                        onClick={() => openEdit(s)}
                        className="p-1.5 rounded text-gray-400 hover:text-terracotta-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                        title="Edit"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleDelete(s.key)}
                        className="p-1.5 rounded text-gray-400 hover:text-red-500 hover:bg-gray-100 dark:hover:bg-gray-700"
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Create / Edit Modal ──────────────────────────── */}
      {showForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="bg-gray-100 dark:bg-gray-800 rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-4">
              {editKey ? `Edit: ${editKey}` : 'Add Secret'}
            </h3>

            {!editKey && (
              <div className="mb-3">
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Key
                </label>
                <input
                  value={formKey}
                  onChange={(e) => setFormKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ''))}
                  placeholder="e.g. OPENAI_API_KEY"
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 font-mono text-sm focus:ring-2 focus:ring-focus focus:border-transparent"
                />
              </div>
            )}

            <div className="mb-3">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Value {editKey && <span className="text-gray-400 font-normal">(leave empty to keep current)</span>}
              </label>
              <input
                type="password"
                value={formValue}
                onChange={(e) => setFormValue(e.target.value)}
                placeholder={editKey ? 'Enter new value...' : 'Enter secret value...'}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 font-mono text-sm focus:ring-2 focus:ring-focus focus:border-transparent"
              />
            </div>

            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Description
              </label>
              <input
                value={formDesc}
                onChange={(e) => setFormDesc(e.target.value)}
                placeholder="What is this secret for?"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-focus focus:border-transparent"
              />
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowForm(false)}
                className="px-4 py-2 text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={createMutation.isPending || updateMutation.isPending}
                className="px-4 py-2 text-sm bg-terracotta-500 text-gray-100 rounded-lg hover:bg-terracotta-600 disabled:opacity-50"
              >
                {editKey ? 'Update' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
