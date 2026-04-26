import { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, ChevronDown, Search, ArrowLeft, Library, ExternalLink } from 'lucide-react'
import { api } from '../api/client'
import MarkdownRenderer from '../components/common/MarkdownRenderer'
import AuthImage from '../components/common/AuthImage'
import type { DocSite, DocSiteSection, DocPage } from '../types'

// ── Link path normalization ─────────────────────────────

function normalizeDocPath(href: string, currentPagePath: string): { path: string; hash: string } {
  const hashIndex = href.indexOf('#')
  const hash = hashIndex >= 0 ? href.substring(hashIndex) : ''
  let path = hashIndex >= 0 ? href.substring(0, hashIndex) : href

  if (!path) return { path: '', hash }

  const isAbsolute = path.startsWith('/')

  // Strip leading slash
  if (isAbsolute) {
    path = path.substring(1)
  }

  // Strip .md extension
  if (path.endsWith('.md')) {
    path = path.slice(0, -3)
  }

  // Strip trailing slash
  if (path.endsWith('/')) {
    path = path.slice(0, -1)
  }

  // Resolve relative paths against current page's directory
  if (!isAbsolute && currentPagePath) {
    const dir = currentPagePath.includes('/')
      ? currentPagePath.substring(0, currentPagePath.lastIndexOf('/'))
      : ''
    path = dir ? `${dir}/${path}` : path
  }

  // Resolve ../ and ./ segments
  const segments = path.split('/')
  const resolved: string[] = []
  for (const seg of segments) {
    if (seg === '..') {
      resolved.pop()
    } else if (seg !== '.' && seg !== '') {
      resolved.push(seg)
    }
  }

  return { path: resolved.join('/'), hash }
}

// ── Tree Node Component ──────────────────────────────────

interface TreeNodeProps {
  section: DocSiteSection
  siteId: string
  activePath: string | null
  activeNodeKey: string | null
  nodeKey: string
  onSelect: (path: string, nodeKey: string) => void
  expandedKeys: Set<string>
  onToggle: (nodeKey: string, expand: boolean) => void
  depth?: number
}

function TreeNode({ section, siteId, activePath, activeNodeKey, nodeKey, onSelect, expandedKeys, onToggle, depth = 0 }: TreeNodeProps) {
  const hasChildren = section.children.length > 0
  const isActive = activeNodeKey === nodeKey
  const expanded = expandedKeys.has(nodeKey)

  const handleClick = () => {
    if (section.path) {
      onSelect(section.path, nodeKey)
      // When selecting a page, always expand children (don't collapse)
      if (hasChildren && !expanded) onToggle(nodeKey, true)
    } else if (hasChildren) {
      // Category-only nodes: toggle expand/collapse
      onToggle(nodeKey, !expanded)
    }
  }

  return (
    <div>
      <button
        onClick={handleClick}
        className={`w-full flex items-center gap-1 px-2 py-1.5 text-left text-sm rounded-md transition-colors ${
          isActive
            ? 'bg-accent-50 text-accent-700 dark:bg-accent-900/30 dark:text-accent-300 font-medium'
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
          {section.children.map((child, i) => {
            const childKey = `${nodeKey}/${child.path ?? `${child.title}-${i}`}`
            return (
              <TreeNode
                key={childKey}
                section={child}
                siteId={siteId}
                activePath={activePath}
                activeNodeKey={activeNodeKey}
                nodeKey={childKey}
                onSelect={onSelect}
                expandedKeys={expandedKeys}
                onToggle={onToggle}
                depth={depth + 1}
              />
            )
          })}
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

// ── Internal link component ──────────────────────────────

function isExternalHref(href: string): boolean {
  return /^https?:\/\//.test(href) || href.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(href)
}

function useDocSiteLink(siteId: string, pagePath: string | undefined) {
  const navigate = useNavigate()

  return useCallback(
    ({ href, children: linkChildren }: { href?: string; children?: React.ReactNode }) => {
      if (!href) return <span>{linkChildren}</span>

      const linkClass = 'text-accent-600 hover:text-accent-800 dark:text-accent-400 dark:hover:text-accent-300 underline'

      // External links (http, https, mailto, tel, etc.) → new tab
      if (isExternalHref(href)) {
        return (
          <a href={href} target="_blank" rel="noopener noreferrer" className={`${linkClass} inline-flex items-center gap-0.5`}>
            {linkChildren}
            <ExternalLink className="w-3 h-3 inline flex-shrink-0" />
          </a>
        )
      }

      // Anchor-only links → scroll within page
      if (href.startsWith('#')) {
        return <a href={href} className={linkClass}>{linkChildren}</a>
      }

      // Internal link → normalize path and navigate within the DocSite
      const { path: targetPath, hash } = normalizeDocPath(href, pagePath ?? '')
      const fullUrl = `/docsites/${siteId}/${targetPath}${hash}`

      const handleClick = (e: React.MouseEvent) => {
        e.preventDefault()
        navigate(fullUrl)
      }

      return <a href={fullUrl} onClick={handleClick} className={linkClass}>{linkChildren}</a>
    },
    [siteId, pagePath, navigate],
  )
}

// ── Main Page Component ──────────────────────────────────

export default function DocSiteViewerPage() {
  const { siteId, '*': pagePath } = useParams<{ siteId: string; '*': string }>()
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [mobileShowContent, setMobileShowContent] = useState(!!pagePath)
  const [activeNodeKey, setActiveNodeKey] = useState<string | null>(null)
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set())
  const DocSiteLink = useDocSiteLink(siteId!, pagePath)

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

  // Scroll to hash after page content loads
  useEffect(() => {
    if (page && window.location.hash) {
      const id = window.location.hash.substring(1)
      requestAnimationFrame(() => {
        const el = document.getElementById(id)
        el?.scrollIntoView({ behavior: 'smooth' })
      })
    }
  }, [page])

  const { data: searchResults } = useQuery({
    queryKey: ['docsite-search', siteId, searchQuery],
    queryFn: () => api.get(`/docsites/${siteId}/search`, { params: { q: searchQuery, limit: 30 } }).then((r) => r.data),
    enabled: !!siteId && searchQuery.length >= 2,
  })

  const handleToggle = useCallback((nodeKey: string, expand: boolean) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev)
      if (expand) next.add(nodeKey)
      else next.delete(nodeKey)
      return next
    })
  }, [])

  // Find nodeKey for a pagePath by walking the section tree, returning ancestor keys
  const findAncestorKeys = useCallback((sections: DocSiteSection[], targetPath: string) => {
    const ancestors: string[] = []
    function walk(nodes: DocSiteSection[], parentKey: string): string | null {
      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i]
        const key = parentKey
          ? `${parentKey}/${node.path ?? `${node.title}-${i}`}`
          : node.path ?? `${node.title}-${i}`
        if (node.path === targetPath) return key
        if (node.children.length > 0) {
          ancestors.push(key)
          const found = walk(node.children, key)
          if (found) return found
          ancestors.pop()
        }
      }
      return null
    }
    const nodeKey = walk(sections, '')
    return { nodeKey, ancestors }
  }, [])

  // Initialize expanded keys when site loads or pagePath changes on fresh mount
  const initializedRef = useRef(false)
  useEffect(() => {
    const sections = site?.sections
    if (!sections || initializedRef.current) return
    initializedRef.current = true

    if (pagePath) {
      const { nodeKey, ancestors } = findAncestorKeys(sections, pagePath)
      if (nodeKey) {
        setActiveNodeKey(nodeKey)
        setExpandedKeys(new Set(ancestors))
      }
    } else if (sections.length > 0) {
      const firstKey = sections[0].path ?? `${sections[0].title}-0`
      setExpandedKeys(new Set([firstKey]))
    }
  }, [site?.sections, pagePath, findAncestorKeys])

  const handleSelectPage = useCallback(
    (path: string, nodeKey: string) => {
      navigate(`/docsites/${siteId}/${path}`)
      setActiveNodeKey(nodeKey)
      setMobileShowContent(true)
      // Expand ancestors of the selected node
      setExpandedKeys((prev) => {
        const next = new Set(prev)
        const parts = nodeKey.split('/')
        let current = ''
        for (let i = 0; i < parts.length - 1; i++) {
          current = current ? `${current}/${parts[i]}` : parts[i]
          next.add(current)
        }
        return next
      })
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
        className={`w-72 flex-shrink-0 bg-gray-100 dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col overflow-hidden ${
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
              <Library className="w-4 h-4 text-accent-500 dark:text-accent-400 flex-shrink-0" />
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
              className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-focus"
            />
          </div>
        </div>

        {/* Tree */}
        <nav className="flex-1 overflow-y-auto py-2 px-1">
          {filteredSections.map((section, i) => {
            const rootKey = section.path ?? `${section.title}-${i}`
            return (
              <TreeNode
                key={rootKey}
                section={section}
                siteId={siteId!}
                activePath={pagePath ?? null}
                activeNodeKey={activeNodeKey}
                nodeKey={rootKey}
                onSelect={handleSelectPage}
                expandedKeys={expandedKeys}
                onToggle={handleToggle}
              />
            )
          })}
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
              <MarkdownRenderer componentOverrides={{
                img: ({ src, alt, ...rest }) => <AuthImage src={src} alt={alt} {...rest} />,
                a: DocSiteLink as any,
              }}>{processedContent}</MarkdownRenderer>
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
                className="flex items-center gap-1 text-sm text-accent-500 hover:text-accent-600 mt-2"
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
