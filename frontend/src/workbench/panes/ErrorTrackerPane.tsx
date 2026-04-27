import { ErrorTrackerView } from '../../pages/ErrorTrackerPage'
import type { PaneComponentProps } from '../paneRegistry'

/**
 * Error tracker pane (Phase C2 D1-b/3). Adapter around the extracted
 * ``ErrorTrackerView`` body — the same UI used by
 * ``/projects/:id?view=errors`` on the legacy ProjectPage, but
 * decoupled from ``useParams()`` so it works inside a Workbench pane
 * with whatever ``projectId`` the pane was created in.
 *
 * The pane carries no per-pane state today (filter / selection live
 * inside ``ErrorTrackerView`` as React state, intentional —
 * different pane instances of the same project should be able to
 * inspect different issues independently). Persisting the selected
 * issue across reloads is left for a follow-up if requested.
 */
export default function ErrorTrackerPane({
  paneId,
  projectId,
}: PaneComponentProps<'error-tracker'>) {
  void paneId // No subscriptions yet; kept for signature parity.
  return (
    <div className="h-full flex flex-col overflow-hidden">
      <ErrorTrackerView projectId={projectId} />
    </div>
  )
}
