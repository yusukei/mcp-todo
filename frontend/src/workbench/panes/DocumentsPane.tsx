import { useCallback } from 'react'
import ProjectDocumentsTab from '../../components/project/ProjectDocumentsTab'
import type { PaneComponentProps } from '../paneRegistry'
import { useWorkbenchEventBus } from '../eventBus'

/**
 * Documents pane (Phase C2 D1-b/2). Adapter around the existing
 * ``ProjectDocumentsTab`` (CRUD / sort / import / export / version
 * history). The tab already supports an external ``onSelectId``
 * callback that suppresses its own ``useNavigate`` URL writes — we
 * wire that through ``paneConfig.docId`` so the selected document
 * survives a reload, and emit ``open-doc`` so any DocPane in the
 * layout picks up the same selection (cross-pane events §5.3).
 *
 * The wrapped component renders its own internal split (list +
 * detail) — when paired with a separate DocPane the user gets two
 * independent viewers (e.g. compare two documents side-by-side).
 */
export default function DocumentsPane({
  paneId,
  projectId,
  paneConfig,
  onConfigChange,
}: PaneComponentProps<'documents'>) {
  void paneId // DocumentsPane only emits; subscriptions live in DocPane.
  const config = paneConfig
  const bus = useWorkbenchEventBus()

  const handleSelectId = useCallback(
    (id: string | null) => {
      // Persist selection so reload restores it.
      onConfigChange({ docId: id ?? undefined })
      // Notify any DocPane in the layout. The bus picks the focused /
      // most-recently-focused / first DocPane (§5.3); if none exist
      // the selected doc still shows in our internal detail panel.
      if (id) {
        bus.emit('open-doc', { docId: id })
      }
    },
    [onConfigChange, bus],
  )

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <ProjectDocumentsTab
        projectId={projectId}
        initialDocumentId={config.docId}
        onSelectId={handleSelectId}
      />
    </div>
  )
}
