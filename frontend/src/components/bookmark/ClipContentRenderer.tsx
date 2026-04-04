import { useEffect, useRef, useMemo } from 'react'
import DOMPurify from 'dompurify'
import { api } from '../../api/client'
import MarkdownRenderer from '../common/MarkdownRenderer'
import AuthImage from '../common/AuthImage'

interface Props {
  content: string
}

/**
 * Renders clipped bookmark content.
 * Auto-detects HTML vs Markdown:
 *   - If content starts with '<' or contains common HTML tags → render as sanitized HTML
 *   - Otherwise → render as Markdown
 *
 * For HTML content, images pointing to /api/v1/bookmark-assets/ are fetched
 * with JWT authentication and replaced with blob URLs.
 */
export default function ClipContentRenderer({ content }: Props) {
  const isHtml = useMemo(() => {
    const trimmed = content.trimStart()
    return trimmed.startsWith('<') || /<(?:div|p|h[1-6]|article|section|img|ul|ol|table|blockquote)\b/i.test(trimmed)
  }, [content])

  if (!isHtml) {
    return (
      <MarkdownRenderer
        componentOverrides={{
          img: ({ src, alt }) => (
            <AuthImage src={src} alt={alt ?? ''} className="max-w-full rounded my-2" />
          ),
        }}
      >
        {content}
      </MarkdownRenderer>
    )
  }

  return <HtmlRenderer html={content} />
}


function HtmlRenderer({ html }: { html: string }) {
  const containerRef = useRef<HTMLDivElement>(null)

  const sanitized = useMemo(() => {
    return DOMPurify.sanitize(html, {
      ADD_TAGS: ['iframe'],
      ADD_ATTR: ['target', 'allowfullscreen', 'frameborder', 'loading'],
      ALLOW_DATA_ATTR: false,
      FORBID_TAGS: ['script', 'style', 'form', 'input', 'textarea', 'select'],
    })
  }, [html])

  useEffect(() => {
    if (!containerRef.current) return

    // Find all internal images and replace with authenticated fetches
    const imgs = containerRef.current.querySelectorAll('img')
    const controllers: AbortController[] = []

    imgs.forEach((img) => {
      const src = img.getAttribute('src')
      if (!src || !src.startsWith('/api/')) return

      const controller = new AbortController()
      controllers.push(controller)

      // Add loading placeholder style
      img.style.minHeight = '100px'
      img.style.background = 'var(--tw-gradient-from, #e5e7eb)'
      img.style.borderRadius = '0.375rem'

      api
        .get(src.replace('/api/v1', ''), {
          responseType: 'blob',
          signal: controller.signal,
        })
        .then((res) => {
          const blobUrl = URL.createObjectURL(res.data)
          img.src = blobUrl
          img.style.minHeight = ''
          img.style.background = ''
          // Cleanup on unmount handled by effect return
          img.dataset.blobUrl = blobUrl
        })
        .catch(() => {
          img.alt = img.alt || '[画像を読み込めません]'
          img.style.minHeight = '2rem'
          img.style.background = ''
        })
    })

    // Make all links open in new tab
    containerRef.current.querySelectorAll('a').forEach((a) => {
      if (a.href && !a.href.startsWith('#')) {
        a.target = '_blank'
        a.rel = 'noopener noreferrer'
      }
    })

    return () => {
      controllers.forEach((c) => c.abort())
      // Revoke blob URLs
      if (containerRef.current) {
        containerRef.current.querySelectorAll('img[data-blob-url]').forEach((img) => {
          const blobUrl = (img as HTMLImageElement).dataset.blobUrl
          if (blobUrl) URL.revokeObjectURL(blobUrl)
        })
      }
    }
  }, [sanitized])

  return (
    <>
      <style>{clipHtmlStyles}</style>
      <div
        ref={containerRef}
        className="clip-html-content prose prose-sm prose-gray dark:prose-invert max-w-none"
        dangerouslySetInnerHTML={{ __html: sanitized }}
      />
    </>
  )
}


// CSS for site-specific HTML structures
const clipHtmlStyles = `
/* ── Zenn scrap comment cards ────────────────── */
.clip-comment-card {
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 0.75rem;
  margin-bottom: 1rem;
  overflow: hidden;
  background: rgba(148, 163, 184, 0.03);
}
.clip-comment-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid rgba(148, 163, 184, 0.1);
  font-size: 0.8125rem;
}
.clip-avatar {
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 50%;
  object-fit: cover;
}
.clip-date {
  color: #94a3b8;
  font-size: 0.75rem;
  margin-left: auto;
}
.clip-comment-body {
  padding: 0.75rem 1rem;
}
.clip-comment-body > *:first-child { margin-top: 0; }
.clip-comment-body > *:last-child { margin-bottom: 0; }

/* ── Embedded link cards ─────────────────────── */
.clip-html-content .embed-zenn-link,
.clip-html-content [class*="EmbedLink"],
.clip-html-content [class*="linkCard"],
.clip-html-content [class*="embed-card"] {
  display: block;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 0.5rem;
  padding: 0.75rem;
  margin: 0.75rem 0;
  text-decoration: none !important;
  transition: background-color 0.15s;
}
.clip-html-content .embed-zenn-link:hover,
.clip-html-content [class*="EmbedLink"]:hover {
  background: rgba(148, 163, 184, 0.05);
}

/* ── Hide broken external images gracefully ──── */
.clip-html-content img {
  max-width: 100%;
  border-radius: 0.375rem;
  margin: 0.5rem 0;
}
.clip-html-content img[src^="http"]:not([src*="/api/"]) {
  /* External images that might be blocked by CSP */
  display: none;
}
.clip-html-content img[data-blob-url] {
  display: block !important;
}

/* ── Twitter/X embed cards ────────────────────── */
.clip-tweet-embed {
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 0.75rem;
  padding: 1rem;
  margin: 1rem 0;
  background: rgba(148, 163, 184, 0.04);
}
.clip-tweet-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}
.clip-tweet-author {
  font-weight: 600;
  font-size: 0.875rem;
}
.clip-tweet-icon {
  font-size: 1.25rem;
  opacity: 0.5;
}
.clip-tweet-body {
  font-size: 0.9375rem;
  line-height: 1.5;
  margin-bottom: 0.5rem;
}
.clip-tweet-body p {
  margin: 0 !important;
}
.clip-tweet-date {
  color: #94a3b8;
  font-size: 0.75rem;
  margin-bottom: 0.5rem;
}
.clip-tweet-link a {
  color: #1d9bf0 !important;
  text-decoration: none !important;
  font-size: 0.8125rem;
}
.clip-tweet-link a:hover {
  text-decoration: underline !important;
}

/* ── YouTube embeds ──────────────────────────── */
.clip-youtube-embed {
  margin: 0.75rem 0;
}
.clip-html-content iframe[src*="youtube"],
.clip-html-content iframe[src*="youtu.be"],
.clip-youtube-embed iframe {
  width: 100%;
  aspect-ratio: 16/9;
  border: none;
  border-radius: 0.5rem;
}
`
