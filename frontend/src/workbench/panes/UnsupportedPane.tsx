import { HelpCircle } from 'lucide-react'
import type { PaneComponentProps } from '../paneRegistry'

/** Renders when the persisted layout references a pane type the
 *  current build doesn't ship. We keep the layout structure intact
 *  (so a downgrade-and-upgrade cycle preserves user intent) and
 *  surface a clear message rather than silently dropping the pane. */
export default function UnsupportedPane({ paneConfig }: PaneComponentProps<'unsupported'>) {
  const originalType = paneConfig.originalType
  return (
    <div className="h-full flex flex-col items-center justify-center gap-3 p-6 text-center bg-status-hold/10">
      <HelpCircle className="w-8 h-8 text-status-hold" />
      <div>
        <p className="text-sm font-medium text-status-hold font-serif">
          Unsupported pane type
        </p>
        {originalType && (
          <p className="text-xs text-gray-200 mt-1">
            Originally: <code className="font-mono">{originalType}</code>
          </p>
        )}
        <p className="text-xs text-gray-300 mt-2 max-w-md">
          The persisted layout references a pane type this build
          doesn't know about. Close this tab and use the{' '}
          <strong>+ (Add tab)</strong> button to add a supported one.
        </p>
      </div>
    </div>
  )
}
