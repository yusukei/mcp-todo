import { useState, useMemo, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, ChevronDown, Search, ArrowLeft, Library, ExternalLink } from 'lucide-react'
import { api } from '../api/client'
import MarkdownRenderer from '../components/common/MarkdownRenderer'
import type { DocSite, DocSiteSection, DocPage } from '../types'

// ── Tree Node Component ──────────────────────────────────

interface TreeNodeProps {
  section: DocSiteSection
  siteId: string
  activePath: string | null
  onSelect: (path: string) => void
  depth?: number
  defaultExpanded?: boolean
}

function TreeNode({ section, siteId, activePath, onSelect, depth = 0, defaultExpanded = false }: TreeNodeProps) {
  const hasChildren = section.children.length > 0
  const isActive = section.path !== null && section.path === activePath

  // Expand if this node or any descendant is active
  const isDescendantActive = useMemo(() => {
    function check(s: DocSiteSection): boolean {
      if (s.path === activePath) return true
      return s.children.some(check)
    }
    return check(section)
  }, [section, activePath])

  const [expanded, setExpanded] = useState(defaultExpanded || isDescendantActive)

  const handleClick = () => {
    if (section.path) {
      onSelect(section.path)
    }
    if (hasChildren) {
      setExpanded(!expanded)
    }
  }

  return (
    <div>
      <button
        onClick={handleClick}
        className={`w-full flex items-center gap-1 px-2 py-1.5 text-left text-sm rounded-md transition-colors ${
          isActive
            ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300 font-medium'
            : section.path
              ? 'text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700/50'
              : 'text-gray-500 dark:text-gray-400 font-semibold text-xs uppercase tracking-wide'
        }`}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        {hasChildren ? (
          expanded ? (
            <ChevronDown className="w-3.5 h-3.5 flex-shrink-0 text-gray-400" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 text-gray-400" />
          )
        ) : (
          <span className="w-3.5 flex-shrink-0" />
        )}
        <span className="truncate">{section.title}</span>
      </button>
      {hasChildren && expanded && (
        <div>
          {section.children.map((child, i) => (
            <TreeNode
              key={child.path ?? `${child.title}-${i}`}
              section={child}
              siteId={siteId}
              activePath={activePath}
              onSelect={onSelect}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Image URL rewriter for MarkdownRenderer ──────────────

function rewriteImageUrls(content: string, siteId: string, pagePath: string): string {
  // Rewrite relative image references like `slug/images/img_001.webp`
  // to API URLs like `/api/v1/docsites/{siteId}/assets/{dir}/slug/images/img_001.webp`
  const pageDir = pagePath.includes('/') ? pagePath.substring(0, pagePath.lastIndexOf('/')) : ''

  return content.replace(
    /!\[([^\]]*)\]\(([^)]+)\)/g,
    (match, alt, src) => {
      // Skip absolute URLs
      if (src.startsWith('http://') || src.startsWith('https://')) return match
      // Build full asset path
      const assetPath = pageDir ? `${pageDir}/${src}` : src
      return `![${alt}](/api/v1/docsites/${siteId}/assets/${assetPath})`
    }
  )
}

// ── Main Page Component ──────────────────────────────────

export default function DocSiteViewerPage() {
  const { siteId, '*': pagePath } = useParams<{ siteId: string; '*': string }>()
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [mobileShowContent, setMobileShowContent] = useState(!!pagePath)

  const { data: site, isLoading: siteLoading } = useQuery<DocSite>({
    queryKey: ['docsite', siteId],
    queryFn: () => api.get(`/docsites/${siteId}`).then((r) => r.data),
    enabled: !!siteId,
  })

  const { data: page, isLoading: pageLoading } = useQuery<DocPage>({
    queryKey: ['docpage', siteId, pagePath],
    queryFn: () => api.get(`/docsites/${siteId}/pages/${pagePath}`).then((r) => r.data),
    enabled: !!siteId && !!pagePath,
  })

  const { data: searchResults } = useQuery({
    queryKey: ['docsite-search', siteId, searchQuery],
    queryFn: () => api.get(`/docsites/${siteId}/search`, { params: { q: searchQuery, limit: 30 } }).then((r) => r.data),
    enabled: !!siteId && searchQuery.length >= 2,
  })

  const handleSelectPage = useCallback(
    (path: string) => {
      navigate(`/docsites/${siteId}/${path}`)
      setMobileShowContent(true)
    },
    [siteId, navigate],
  )

  // Filter tree nodes by search
  const filteredSections = useMemo(() => {
    if (!site?.sections || !searchQuery || searchQuery.length < 2) return site?.sections ?? []

    if (searchResults?.items) {
      const matchPaths = new Set(searchResults.items.map((item: any) => item.path))

      function filterSection(s: DocSiteSection): DocSiteSection | null {
        const childrenFiltered = s.children.map(filterSection).filter(Boolean) as DocSiteSection[]
        if (s.path && matchPaths.has(s.path)) return { ...s, children: childrenFiltered }
        if (childrenFiltered.length > 0) return { ...s, children: childrenFiltered }
        return null
      }

      return site.sections.map(filterSection).filter(Boolean) as DocSiteSection[]
    }
    return site?.sections ?? []
  }, [site?.sections, searchQuery, searchResults])

  if (siteLoading) {
    return <div className="flex-1 flex items-center justify-center text-gray-500 dark:text-gray-400">読み込み中...</div>
  }

  if (!site) {
    return <div className="flex-1 flex items-center justify-center text-gray-500 dark:text-gray-400">サイトが見つかりません</div>
  }

  const processedContent = page ? rewriteImageUrls(page.content, siteId!, page.path) : ''

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Sidebar with tree nav */}
      <div
        className={`w-72 flex-shrink-0 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col overflow-hidden ${
          mobileShowContent ? 'hidden md:flex' : 'flex'
        }`}
      >
        {/* Header */}
        <div className="px-3 py-3 border-b border-gray-100 dark:border-gray-700">
          <div className="flex items-center gap-2 mb-2">
            <Link
              to="/docsites"
              className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              <ArrowLeft className="w-4 h-4" />
            </Link>
            <div className="flex items-center gap-1.5 min-w-0">
              <Library className="w-4 h-4 text-indigo-500 dark:text-indigo-400 flex-shrink-0" />
              <span className="font-semibold text-sm text-gray-800 dark:text-gray-100 truncate">{site.name}</span>
            </div>
          </div>
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="検索..."
              className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            />
          </div>
        </div>

        {/* Tree */}
        <nav className="flex-1 overflow-y-auto py-2 px-1">
          {filteredSections.map((section, i) => (
            <TreeNode
              key={section.path ?? `${section.title}-${i}`}
              section={section}
              siteId={siteId!}
              activePath={pagePath ?? null}
              onSelect={handleSelectPage}
              defaultExpanded={i === 0 && !pagePath}
            />
          ))}
        </nav>
      </div>

      {/* Content area */}
      <div
        className={`flex-1 overflow-y-auto ${
          !mobileShowContent ? 'hidden md:block' : ''
        }`}
      >
        {pagePath ? (
          pageLoading ? (
            <div className="p-8 text-gray-500 dark:text-gray-400">読み込み中...</div>
          ) : page ? (
            <div className="max-w-4xl mx-auto px-6 py-6 md:px-8 md:py-8">
              {/* Mobile back button */}
              <button
                onClick={() => setMobileShowContent(false)}
                className="md:hidden flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 mb-4 hover:text-gray-700 dark:hover:text-gray-300"
              >
                <ArrowLeft className="w-4 h-4" />
                目次に戻る
              </button>
              <MarkdownRenderer>{processedContent}</MarkdownRenderer>
            </div>
          ) : (
            <div className="p-8 text-gray-500 dark:text-gray-400">ページが見つかりません</div>
          )
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-gray-400 dark:text-gray-500 p-8">
            <Library className="w-16 h-16 mb-4 text-gray-200 dark:text-gray-700" />
            <p className="text-lg font-medium text-gray-500 dark:text-gray-400">{site.name}</p>
            {site.description && <p className="text-sm mt-1">{site.description}</p>}
            <p className="text-sm mt-4">{site.page_count} ページ</p>
            {site.source_url && (
              <a
                href={site.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-sm text-indigo-500 hover:text-indigo-600 mt-2"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                元のサイト
              </a>
            )}
            <p className="text-xs mt-6">左のサイドバーからページを選択してください</p>
          </div>
        )}
      </div>
    </div>
  )
}
