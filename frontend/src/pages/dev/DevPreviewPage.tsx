/**
 * Dev-only design-system preview page.
 *
 * Phase 1.5 of the UI redesign — gives us a single URL where we can
 * eyeball every Phase 1 token (colors, fonts, utilities) and watch the
 * Variant B / Admin / TaskDetail builds slot in as Phase 2-6 lands.
 *
 * Mounted only when ``import.meta.env.DEV`` is true; the production
 * bundle short-circuits the import to ``null`` (see App.tsx) so this
 * file is tree-shaken away.
 */
import { Link } from 'react-router-dom'

const ACCENT_SHADES = [
  '50',
  '100',
  '200',
  '300',
  '400',
  '500',
  '600',
  '700',
  '800',
  '900',
  '950',
] as const

const GRAY_SHADES = ACCENT_SHADES

const SEMANTIC_TOKENS: Array<{ name: string; bg: string }> = [
  { name: 'status-todo', bg: 'bg-status-todo' },
  { name: 'status-progress', bg: 'bg-status-progress' },
  { name: 'status-hold', bg: 'bg-status-hold' },
  { name: 'status-done', bg: 'bg-status-done' },
  { name: 'status-cancel', bg: 'bg-status-cancel' },
  { name: 'pri-urgent', bg: 'bg-pri-urgent' },
  { name: 'pri-high', bg: 'bg-pri-high' },
  { name: 'pri-medium', bg: 'bg-pri-medium' },
  { name: 'pri-low', bg: 'bg-pri-low' },
  { name: 'decision', bg: 'bg-decision' },
  { name: 'approved', bg: 'bg-approved' },
  { name: 'blocked', bg: 'bg-blocked' },
  { name: 'focus', bg: 'bg-focus' },
]

const VARIANT_PLACEHOLDERS: Array<{ id: string; label: string; phase: string; note: string }> = [
  {
    id: 'variant-b',
    label: 'Variant B — Editorial Split',
    phase: 'Phase 2 + 3',
    note: '260px サイドバー + 2 ペイン (Tasks / Detail)。Workbench ヘッダー削除済み。',
  },
  {
    id: 'variant-b-detail',
    label: 'Variant B — TaskDetail focus (decision)',
    phase: 'Phase 5',
    note: 'metaRail prop で右レール 260px 表示、判断コンテキスト紫罫線ブロック。',
  },
  {
    id: 'admin-a',
    label: 'Admin A — Members table',
    phase: 'Phase 6',
    note: 'status / last_active_at / ai_runs_30d / projects_count を表示するテーブル。',
  },
  {
    id: 'user-detail',
    label: 'Admin — User detail',
    phase: 'Phase 6',
    note: 'プロフィール + 30d 統計 + 所属プロジェクト + 最近の AI 実行ログ。',
  },
]

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="mb-12">
      <header className="mb-4">
        <h2 className="font-serif text-2xl text-gray-50 leading-snug">{title}</h2>
        {subtitle && <p className="mt-1 text-sm text-gray-200">{subtitle}</p>}
      </header>
      {children}
    </section>
  )
}

function Swatch({ name, hex, bg }: { name: string; hex?: string; bg: string }) {
  return (
    <div className="flex flex-col gap-1 text-xs">
      <div className={`h-12 rounded ${bg} ring-1 ring-gray-400/30`} title={name} />
      <div className="font-mono text-gray-100">{name}</div>
      {hex && <div className="font-mono text-gray-300">{hex}</div>}
    </div>
  )
}

export default function DevPreviewPage() {
  return (
    <div className="min-h-screen bg-gray-900 text-gray-50 px-8 py-10 font-sans">
      <header className="mb-10">
        <p className="font-mono text-xs text-gray-300 uppercase tracking-widest">
          /dev/preview
        </p>
        <h1 className="font-serif text-4xl mt-1 leading-snug">
          mcp-todo redesign — Phase 1 token showcase
        </h1>
        <p className="mt-3 text-sm text-gray-200 max-w-2xl">
          Dev-only design-system reference. Phase 1 で導入したトークン・フォント・
          ユーティリティクラスがすべてここに見えるよう並べてあります。Phase 2 以降
          の variant 実装は下のセクションに順次差し込まれていきます。
        </p>
        <div className="mt-4">
          <Link
            to="/projects"
            className="text-sm text-accent-400 hover:text-accent-300 underline underline-offset-2"
          >
            ← back to /projects
          </Link>
        </div>
      </header>

      <Section
        title="Typography"
        subtitle="Fraunces (serif) / Noto Sans JP (sans) / JetBrains Mono — Google Fonts loaded with display=swap."
      >
        <div className="grid gap-6 md:grid-cols-3">
          <div>
            <div className="font-mono text-xs text-gray-300">font-serif</div>
            <div className="font-serif text-3xl text-gray-50 mt-1 leading-snug">
              Workbench レイアウト永続化
            </div>
            <div className="font-serif text-base text-gray-100 mt-2 leading-relaxed">
              The quick brown fox jumps over the lazy dog. 検証 → 開発フェーズ。
            </div>
          </div>
          <div>
            <div className="font-mono text-xs text-gray-300">font-sans</div>
            <div className="font-sans text-3xl text-gray-50 mt-1 leading-snug">
              Workbench レイアウト永続化
            </div>
            <div className="font-sans text-base text-gray-100 mt-2 leading-relaxed">
              The quick brown fox jumps over the lazy dog. パイプライン刷新（Compiler/Reviewer）。
            </div>
          </div>
          <div>
            <div className="font-mono text-xs text-gray-300">font-mono</div>
            <div className="font-mono text-3xl text-gray-50 mt-1 leading-snug">
              T8412 / decision_id
            </div>
            <div className="font-mono text-base text-gray-100 mt-2 leading-relaxed">
              const tree = await loadLayout(projectId);
            </div>
          </div>
        </div>
      </Section>

      <Section
        title="Accent (pink) palette"
        subtitle="terracotta から差し替え。primary CTA は accent-500、hover は accent-400、pressed は accent-600。"
      >
        <div className="grid grid-cols-6 md:grid-cols-11 gap-3">
          {ACCENT_SHADES.map((s) => (
            <Swatch key={s} name={`accent-${s}`} bg={`bg-accent-${s}`} />
          ))}
        </div>
      </Section>

      <Section
        title="Gray (Monokai Pro) palette"
        subtitle="gray-50 = primary text, gray-950 = outer void。Tailwind 慣習に合わせて段階的に暗くなる。"
      >
        <div className="grid grid-cols-6 md:grid-cols-11 gap-3">
          {GRAY_SHADES.map((s) => (
            <Swatch key={s} name={`gray-${s}`} bg={`bg-gray-${s}`} />
          ))}
        </div>
      </Section>

      <Section
        title="Semantic tokens"
        subtitle="status / priority / decision / focus — single-shade, used on dots and badges."
      >
        <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
          {SEMANTIC_TOKENS.map((t) => (
            <Swatch key={t.name} name={t.name} bg={t.bg} />
          ))}
        </div>
      </Section>

      <Section
        title="Status dots (with cyan pulse on in_progress)"
      >
        <div className="flex items-center gap-6">
          {(['todo', 'in_progress', 'on_hold', 'done', 'cancelled'] as const).map((s) => (
            <div key={s} className="flex items-center gap-2 text-sm text-gray-100">
              <span className={`status-dot ${s}`} />
              <span className="font-mono text-xs text-gray-200">{s}</span>
            </div>
          ))}
        </div>
      </Section>

      <Section
        title="Pill / Tag / Kbd primitives"
        subtitle="`.pill`, `.tag`, `.kbd` — defined in index.css, ready for Phase 4 TaskCard."
      >
        <div className="flex flex-wrap items-center gap-3">
          <span className="pill bg-gray-700 text-gray-100">
            <span className="dot" />
            pill (default)
          </span>
          <span className="pill bg-decision/20 text-decision">
            <span className="dot" />
            pill (decision)
          </span>
          <span className="tag bg-focus/10 text-focus border border-focus/30">
            workbench
          </span>
          <span className="tag bg-focus/10 text-focus border border-focus/30">
            decision
          </span>
          <span className="kbd text-gray-200">⌘ ↵</span>
          <span className="kbd text-gray-200">Esc</span>
        </div>
      </Section>

      <Section
        title="Paper-grain surface"
        subtitle="Editorial Split の背景に使われるノイズ overlay。host に position: relative + overflow: hidden が必要。"
      >
        <div className="paper-grain relative overflow-hidden h-32 rounded bg-gray-900 ring-1 ring-gray-400/30 flex items-center justify-center">
          <span className="font-serif text-2xl text-gray-50 relative">
            Editorial Split surface
          </span>
        </div>
      </Section>

      <Section
        title="Variants — coming soon"
        subtitle="Phase 2-6 で各 variant の実物がここに差し込まれます。現在はプレースホルダー。"
      >
        <div className="grid gap-4 md:grid-cols-2">
          {VARIANT_PLACEHOLDERS.map((v) => (
            <div
              key={v.id}
              className="rounded-lg border border-gray-400/20 bg-gray-800 p-5"
            >
              <div className="flex items-center justify-between">
                <h3 className="font-serif text-lg text-gray-50">{v.label}</h3>
                <span className="pill bg-gray-700 text-gray-200">
                  <span className="dot" />
                  {v.phase}
                </span>
              </div>
              <p className="mt-2 text-sm text-gray-200 leading-relaxed">{v.note}</p>
              <div className="mt-3 h-32 rounded bg-gray-900 ring-1 ring-gray-400/20 flex items-center justify-center text-xs font-mono text-gray-300">
                placeholder — implement in {v.phase}
              </div>
            </div>
          ))}
        </div>
      </Section>
    </div>
  )
}
