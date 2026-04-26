import { useEffect, useLayoutEffect, useMemo, useState, useRef, useCallback, memo } from 'react'
import clsx from 'clsx'
import { CornerDownRight, ZoomIn, ZoomOut, Maximize2 } from 'lucide-react'
import type { Task } from '../../types'
import {
  computeBarPx,
  computeCriticalPath,
  computeScale,
  fitPxPerMs,
  formatDuration,
  formatTimelineLabel,
  generateTicks,
  groupTasks,
  tickLeftPx,
  type GroupByOption,
  type TaskGroup,
  type TimelineBarPx,
  type TimelineScale,
} from '../../lib/timeline'
import { STATUS_LABELS } from '../../constants/task'

const ROW_HEIGHT = 28
const LABEL_COL_WIDTH = 220
const AXIS_HEIGHT = 28
// Soft floor / ceiling for the user-driven zoom factor so the
// timeline never collapses to 0 px or explodes to GB-scale widths.
const MIN_PX_PER_MS = 1e-9
const MAX_PX_PER_MS = 1e-2

interface Props {
  tasks: Task[]
  projectId: string
  onTaskClick: (taskId: string) => void
  groupBy?: GroupByOption
  onGroupByChange?: (value: GroupByOption) => void
  highlightCritical?: boolean
  onHighlightCriticalChange?: (value: boolean) => void
}

type FlatRow =
  | { kind: 'group-header'; groupKey: string; label: string; count: number }
  | { kind: 'task'; task: Task }

// Phase 4: Monokai status palette. The bar fills the row regardless
// of dark mode (light-mode is intentionally unsupported until Monokai
// Light lands — see UI 再設計仕様書 §6).
const STATUS_BAR_CLASSES: Record<Task['status'], string> = {
  todo: 'bg-status-todo',
  in_progress: 'bg-status-progress motion-safe:animate-pulse',
  on_hold: 'bg-status-hold',
  done: 'bg-status-done',
  cancelled: 'bg-status-cancel timeline-bar-cancelled',
}

export default function TaskTimeline({
  tasks,
  projectId: _projectId,
  onTaskClick,
  groupBy = 'none',
  onGroupByChange,
  highlightCritical = false,
  onHighlightCriticalChange,
}: Props) {
  // Force re-render every 30s so in-progress bar right edges track "now"
  const [, setTick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setTick((x) => x + 1), 30_000)
    return () => clearInterval(id)
  }, [])

  const now = Date.now()
  const scale = useMemo(() => computeScale(tasks, now), [tasks, now])
  const groups = useMemo(() => groupTasks(tasks, groupBy), [tasks, groupBy])
  const ticks = useMemo(() => generateTicks(scale), [scale])
  const critical = useMemo(
    () => (highlightCritical ? computeCriticalPath(tasks, now) : null),
    [tasks, now, highlightCritical],
  )

  const rows = useMemo<FlatRow[]>(() => flattenRows(groups, groupBy), [groups, groupBy])
  const rowIndexById = useMemo(() => {
    const m = new Map<string, number>()
    rows.forEach((r, i) => {
      if (r.kind === 'task') m.set(r.task.id, i)
    })
    return m
  }, [rows])

  const [hoverTask, setHoverTask] = useState<Task | null>(null)
  const [hoverPos, setHoverPos] = useState<{ x: number; y: number } | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // ── Zoom state ─────────────────────────────────────────────
  // ``manualPxPerMs === null`` means "auto-fit": derive pxPerMs so the
  // timeline exactly spans the visible track width. The user can flip
  // to manual via zoom in/out; the Fit button reverts to auto.
  const [manualPxPerMs, setManualPxPerMs] = useState<number | null>(null)
  const [viewportTrackWidth, setViewportTrackWidth] = useState<number>(0)

  // Track the scroll container's inner width so auto-fit can compute
  // pxPerMs from the actual visible track area.
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const update = () => {
      const trackWidth = Math.max(el.clientWidth - LABEL_COL_WIDTH, 0)
      setViewportTrackWidth(trackWidth)
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const autoPxPerMs = useMemo(
    () => fitPxPerMs(scale, viewportTrackWidth),
    [scale, viewportTrackWidth],
  )
  const pxPerMs = manualPxPerMs ?? autoPxPerMs

  // Total widths
  const trackPx = useMemo(
    () => Math.max(scale.span * pxPerMs, viewportTrackWidth),
    [scale.span, pxPerMs, viewportTrackWidth],
  )
  const totalContentWidth = LABEL_COL_WIDTH + trackPx
  const totalRows = rows.length
  const bodyHeight = totalRows * ROW_HEIGHT

  const handleZoomIn = useCallback(() => {
    setManualPxPerMs((cur) => {
      const base = cur ?? autoPxPerMs
      if (base <= 0) return cur
      return Math.min(base * 1.5, MAX_PX_PER_MS)
    })
  }, [autoPxPerMs])
  const handleZoomOut = useCallback(() => {
    setManualPxPerMs((cur) => {
      const base = cur ?? autoPxPerMs
      if (base <= 0) return cur
      return Math.max(base / 1.5, MIN_PX_PER_MS)
    })
  }, [autoPxPerMs])
  const handleFit = useCallback(() => {
    setManualPxPerMs(null)
  }, [])

  const handleBarEnter = useCallback((task: Task, e: React.MouseEvent) => {
    setHoverTask(task)
    setHoverPos({ x: e.clientX, y: e.clientY })
  }, [])
  const handleBarMove = useCallback((e: React.MouseEvent) => {
    setHoverPos({ x: e.clientX, y: e.clientY })
  }, [])
  const handleBarLeave = useCallback(() => {
    setHoverTask(null)
    setHoverPos(null)
  }, [])

  if (tasks.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 dark:text-gray-500">
        タスクがありません
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <TimelineToolbar
        groupBy={groupBy}
        onGroupByChange={onGroupByChange}
        highlightCritical={highlightCritical}
        onHighlightCriticalChange={onHighlightCriticalChange}
        criticalDurationMs={critical?.duration ?? null}
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
        onFit={handleFit}
        zoomMode={manualPxPerMs == null ? 'fit' : 'manual'}
      />

      <div ref={scrollRef} className="flex-1 overflow-auto relative">
        {/* Fixed-size canvas: width covers label col + full timeline,
            height covers axis + all rows. Sticky elements within stay
            visible while the canvas scrolls. */}
        <div
          className="relative"
          style={{
            width: totalContentWidth,
            height: AXIS_HEIGHT + bodyHeight,
          }}
        >
          {/* Axis row — sticky on top so it stays visible while
              scrolling vertically. Inside the canvas so it scrolls
              horizontally with the rest. */}
          <div
            className="sticky top-0 z-20 flex bg-gray-100 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700"
            style={{ height: AXIS_HEIGHT }}
          >
            <div
              className="sticky left-0 z-30 flex items-center px-2 text-xs font-semibold text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700"
              style={{ width: LABEL_COL_WIDTH, flexShrink: 0 }}
            >
              タスク
            </div>
            <div className="relative" style={{ width: trackPx }}>
              {ticks.map((tick) => {
                const left = tickLeftPx(tick.ts, scale, pxPerMs)
                return (
                  <div
                    key={tick.ts}
                    className={clsx(
                      'absolute top-0 bottom-0 border-l',
                      tick.major
                        ? 'border-gray-300 dark:border-gray-600'
                        : 'border-gray-100 dark:border-gray-800',
                    )}
                    style={{ left }}
                  >
                    {tick.major && (
                      <span className="absolute top-0.5 left-1 text-[10px] text-gray-500 dark:text-gray-400 whitespace-nowrap">
                        {formatTimelineLabel(tick.ts, scale.unit)}
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* Body — absolute-positioned rows so virtualization (future)
              can drop in cleanly. */}
          <div
            className="relative"
            style={{ height: bodyHeight, width: totalContentWidth }}
          >
            {rows.map((row, idx) => (
              <TimelineRow
                key={rowKey(row, idx)}
                row={row}
                scale={scale}
                pxPerMs={pxPerMs}
                trackPx={trackPx}
                style={{
                  position: 'absolute',
                  top: idx * ROW_HEIGHT,
                  left: 0,
                  width: totalContentWidth,
                  height: ROW_HEIGHT,
                }}
                onTaskClick={onTaskClick}
                onBarEnter={handleBarEnter}
                onBarMove={handleBarMove}
                onBarLeave={handleBarLeave}
                critical={
                  row.kind === 'task' && critical?.ids.has(row.task.id) === true
                }
                now={now}
              />
            ))}

            <TimelineArrows
              tasks={tasks}
              scale={scale}
              pxPerMs={pxPerMs}
              trackPx={trackPx}
              rowIndexById={rowIndexById}
              criticalIds={critical?.ids ?? null}
            />
          </div>
        </div>
      </div>

      {hoverTask && hoverPos && <TimelineTooltip task={hoverTask} pos={hoverPos} now={now} />}
    </div>
  )
}

function flattenRows(groups: TaskGroup[], groupBy: GroupByOption): FlatRow[] {
  const rows: FlatRow[] = []
  for (const g of groups) {
    if (g.tasks.length === 0) continue
    if (groupBy !== 'none') {
      rows.push({ kind: 'group-header', groupKey: g.key, label: g.label, count: g.tasks.length })
    }
    for (const t of g.tasks) rows.push({ kind: 'task', task: t })
  }
  return rows
}

function rowKey(row: FlatRow, idx: number): string {
  return row.kind === 'task' ? row.task.id : `group:${row.groupKey}:${idx}`
}

interface TimelineRowProps {
  row: FlatRow
  scale: TimelineScale
  pxPerMs: number
  trackPx: number
  style: React.CSSProperties
  onTaskClick: (id: string) => void
  onBarEnter: (task: Task, e: React.MouseEvent) => void
  onBarMove: (e: React.MouseEvent) => void
  onBarLeave: () => void
  critical: boolean
  now: number
}

const TimelineRow = memo(function TimelineRow({
  row,
  scale,
  pxPerMs,
  trackPx,
  style,
  onTaskClick,
  onBarEnter,
  onBarMove,
  onBarLeave,
  critical,
  now,
}: TimelineRowProps) {
  if (row.kind === 'group-header') {
    return (
      <div
        className="flex items-center bg-gray-50 dark:bg-gray-800/80 border-b border-gray-200 dark:border-gray-700 text-xs font-semibold text-gray-600 dark:text-gray-300"
        style={style}
      >
        <div
          className="sticky left-0 z-10 flex items-center px-3 bg-gray-50 dark:bg-gray-800/80 border-r border-gray-200 dark:border-gray-700"
          style={{ width: LABEL_COL_WIDTH, height: '100%', flexShrink: 0 }}
        >
          {row.label}
          <span className="ml-2 text-gray-400 dark:text-gray-500 font-normal">
            {row.count}件
          </span>
        </div>
      </div>
    )
  }

  const task = row.task
  const bar: TimelineBarPx = computeBarPx(task, scale, pxPerMs, now)
  const isSubtask = task.parent_task_id != null

  return (
    <div
      className="flex items-stretch hover:bg-gray-50 dark:hover:bg-gray-700/40"
      style={style}
    >
      <div
        className={clsx(
          'sticky left-0 z-10 flex items-center gap-1.5 px-2 text-xs text-gray-700 dark:text-gray-200 truncate cursor-pointer bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-700',
          isSubtask && 'pl-6 text-gray-500 dark:text-gray-400',
        )}
        style={{ width: LABEL_COL_WIDTH, flexShrink: 0 }}
        onClick={() => onTaskClick(task.id)}
        title={task.title}
      >
        {isSubtask && <CornerDownRight className="w-3 h-3 shrink-0" />}
        <span className="truncate">{task.title}</span>
      </div>
      <div
        className="relative border-b border-gray-100 dark:border-gray-800"
        style={{ width: trackPx, flexShrink: 0 }}
      >
        <button
          type="button"
          data-testid={`timeline-bar-${task.id}`}
          className={clsx(
            'absolute top-1 h-[20px] rounded cursor-pointer transition-shadow',
            STATUS_BAR_CLASSES[task.status],
            critical && 'ring-2 ring-emerald-300 dark:ring-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]',
            'hover:scale-y-110 hover:shadow-md',
          )}
          style={{
            left: bar.leftPx,
            width: bar.widthPx,
          }}
          onMouseEnter={(e) => onBarEnter(task, e)}
          onMouseMove={onBarMove}
          onMouseLeave={onBarLeave}
          onClick={() => onTaskClick(task.id)}
          aria-label={`${task.title} (${STATUS_LABELS[task.status]})`}
        />
      </div>
    </div>
  )
})

interface TimelineArrowsProps {
  tasks: Task[]
  scale: TimelineScale
  pxPerMs: number
  trackPx: number
  rowIndexById: Map<string, number>
  criticalIds: Set<string> | null
}

function TimelineArrows({ tasks, scale, pxPerMs, trackPx, rowIndexById, criticalIds }: TimelineArrowsProps) {
  const edges = useMemo(() => {
    const byId = new Map(tasks.map((t) => [t.id, t]))
    const result: { from: Task; to: Task; critical: boolean }[] = []
    for (const src of tasks) {
      for (const targetId of src.blocks ?? []) {
        const tgt = byId.get(targetId)
        if (!tgt) continue
        const crit =
          criticalIds != null && criticalIds.has(src.id) && criticalIds.has(tgt.id)
        result.push({ from: src, to: tgt, critical: crit })
      }
    }
    return result
  }, [tasks, criticalIds])

  if (edges.length === 0) return null

  return (
    <svg
      aria-hidden
      className="absolute pointer-events-none"
      style={{
        top: 0,
        left: LABEL_COL_WIDTH,
        width: trackPx,
        height: '100%',
      }}
    >
      <defs>
        <marker
          id="timeline-arrow"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto"
        >
          <path d="M0,0 L10,5 L0,10 z" fill="#94a3b8" />
        </marker>
        <marker
          id="timeline-arrow-critical"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto"
        >
          <path d="M0,0 L10,5 L0,10 z" fill="#10b981" />
        </marker>
      </defs>
      {edges.map((edge, i) => {
        const srcIdx = rowIndexById.get(edge.from.id)
        const tgtIdx = rowIndexById.get(edge.to.id)
        if (srcIdx == null || tgtIdx == null) return null
        const srcBar = computeBarPx(edge.from, scale, pxPerMs)
        const tgtBar = computeBarPx(edge.to, scale, pxPerMs)
        const x1 = srcBar.leftPx + srcBar.widthPx
        const x2 = tgtBar.leftPx
        const y1 = srcIdx * ROW_HEIGHT + ROW_HEIGHT / 2
        const y2 = tgtIdx * ROW_HEIGHT + ROW_HEIGHT / 2
        const cp1x = x1 + 40
        const cp2x = x2 - 40
        const d = `M ${x1} ${y1} C ${cp1x} ${y1}, ${cp2x} ${y2}, ${x2} ${y2}`
        return (
          <path
            key={i}
            d={d}
            fill="none"
            stroke={edge.critical ? '#10b981' : '#94a3b8'}
            strokeWidth={edge.critical ? 2 : 1.25}
            markerEnd={
              edge.critical
                ? 'url(#timeline-arrow-critical)'
                : 'url(#timeline-arrow)'
            }
          />
        )
      })}
    </svg>
  )
}

interface TimelineTooltipProps {
  task: Task
  pos: { x: number; y: number }
  now: number
}

function TimelineTooltip({ task, pos, now }: TimelineTooltipProps) {
  const start = new Date(task.created_at).getTime()
  const end = task.completed_at ? new Date(task.completed_at).getTime() : now
  const duration = formatDuration(end - start)
  return (
    <div
      role="tooltip"
      className="fixed z-50 pointer-events-none px-3 py-2 rounded-lg shadow-xl bg-gray-900 text-gray-100 text-xs max-w-xs"
      style={{ left: pos.x + 12, top: pos.y - 12 }}
    >
      <div className="font-semibold truncate">{task.title}</div>
      <div className="mt-1 flex items-center gap-2 text-[11px]">
        <span className="px-1.5 py-0.5 rounded bg-gray-700">
          {STATUS_LABELS[task.status]}
        </span>
        <span className="text-gray-300">{duration}</span>
      </div>
      {task.active_form && (
        <div className="mt-1 text-gray-300 text-[11px] italic truncate">
          {task.active_form}
        </div>
      )}
      {task.assignee_id && (
        <div className="mt-0.5 text-gray-400 text-[11px]">
          担当: {task.assignee_id}
        </div>
      )}
    </div>
  )
}

interface TimelineToolbarProps {
  groupBy: GroupByOption
  onGroupByChange?: (value: GroupByOption) => void
  highlightCritical: boolean
  onHighlightCriticalChange?: (value: boolean) => void
  criticalDurationMs: number | null
  onZoomIn: () => void
  onZoomOut: () => void
  onFit: () => void
  zoomMode: 'fit' | 'manual'
}

function TimelineToolbar({
  groupBy,
  onGroupByChange,
  highlightCritical,
  onHighlightCriticalChange,
  criticalDurationMs,
  onZoomIn,
  onZoomOut,
  onFit,
  zoomMode,
}: TimelineToolbarProps) {
  return (
    <div className="flex items-center gap-3 px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/60 text-sm">
      <label className="flex items-center gap-1.5 text-gray-600 dark:text-gray-300">
        <span className="text-xs">グループ:</span>
        <select
          value={groupBy}
          onChange={(e) => onGroupByChange?.(e.target.value as GroupByOption)}
          className="text-xs border border-gray-200 dark:border-gray-600 rounded px-1.5 py-0.5 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-focus"
        >
          <option value="none">なし</option>
          <option value="assignee">担当者</option>
          <option value="priority">優先度</option>
          <option value="parent">親タスク</option>
          <option value="tag">タグ</option>
        </select>
      </label>
      <label className="flex items-center gap-1.5 text-gray-600 dark:text-gray-300 cursor-pointer">
        <input
          type="checkbox"
          checked={highlightCritical}
          onChange={(e) => onHighlightCriticalChange?.(e.target.checked)}
          className="rounded border-gray-300 dark:border-gray-600 text-accent-600 focus:ring-focus w-3.5 h-3.5"
        />
        <span className="text-xs">クリティカルパス強調</span>
      </label>
      {highlightCritical && criticalDurationMs != null && criticalDurationMs > 0 && (
        <span className="text-xs text-gray-500 dark:text-gray-400">
          最長経路: {formatDuration(criticalDurationMs)}
        </span>
      )}

      {/* Zoom controls (Case A horizontal scroll) */}
      <div className="ml-auto flex items-center gap-1">
        <button
          type="button"
          onClick={onZoomOut}
          aria-label="ズームアウト"
          title="ズームアウト"
          className="p-1 rounded text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <ZoomOut className="w-3.5 h-3.5" />
        </button>
        <button
          type="button"
          onClick={onFit}
          aria-label="ウィンドウに合わせる"
          title="ウィンドウに合わせる"
          className={clsx(
            'p-1 rounded text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700',
            zoomMode === 'fit' && 'text-emerald-600 dark:text-emerald-400',
          )}
        >
          <Maximize2 className="w-3.5 h-3.5" />
        </button>
        <button
          type="button"
          onClick={onZoomIn}
          aria-label="ズームイン"
          title="ズームイン"
          className="p-1 rounded text-gray-500 hover:text-gray-800 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <ZoomIn className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  )
}
