import { useEffect, useRef, useMemo } from 'react'
import DOMPurify from 'dompurify'
import { Tweet } from 'react-tweet'
import { api } from '../../api/client'
import MarkdownRenderer from '../common/MarkdownRenderer'
import AuthImage from '../common/AuthImage'

interface Props {
  content: string
}

// ── Embed detection helpers ─────────────────────────

const YOUTUBE_URL_RE = /^https?:\/\/(?:www\.)?(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]+)/

function TweetEmbed({ tweetId }: { tweetId: string }) {
  return (
    <div className="clip-tweet-wrapper">
      <Tweet id={tweetId} />
    </div>
  )
}

function YouTubeEmbed({ videoId }: { videoId: string }) {
  return (
    <div className="clip-youtube-embed">
      <iframe
        src={`https://www.youtube.com/embed/${videoId}`}
        frameBorder="0"
        allowFullScreen
        loading="lazy"
      />
    </div>
  )
}

// ── Markdown with embed detection ────────────────────

const mdComponentOverrides = {
  img: ({ src, alt }: { src?: string; alt?: string }) => (
    <AuthImage src={src} alt={alt ?? ''} className="max-w-full rounded my-2" />
  ),
  a: ({ href, children: linkChildren }: { href?: string; children?: React.ReactNode }) => {
    if (!href) return <a>{linkChildren}</a>

    const linkText = typeof linkChildren === 'string'
      ? linkChildren
      : Array.isArray(linkChildren)
        ? linkChildren.map((c) => (typeof c === 'string' ? c : '')).join('')
        : ''
    const isBareUrl = linkText.trim() === href ||
      linkText.trim() === href.replace(/^https?:\/\//, '')

    // YouTube video → iframe embed
    const ytMatch = href.match(YOUTUBE_URL_RE)
    if (ytMatch && isBareUrl) {
      return <YouTubeEmbed videoId={ytMatch[1]} />
    }

    return (
      <a href={href} target="_blank" rel="noopener noreferrer"
        className="text-accent-600 hover:text-accent-800 dark:text-accent-400 dark:hover:text-accent-300 underline">
        {linkChildren}
      </a>
    )
  },
}

function MarkdownWithEmbeds({ content }: { content: string }) {
  // Split content by embed markers (tweets and YouTube) and render each segment
  const segments = useMemo(() => {
    type Segment =
      | { type: 'md'; text: string }
      | { type: 'tweet'; tweetId: string }
      | { type: 'youtube'; videoId: string }

    const parts: Segment[] = []
    let lastIndex = 0

    // Match both tweet and YouTube markers
    const regex = /<!--(?:tweet:([^|]+)\|[^>]*|youtube:([\w-]+))-->/g
    let match: RegExpExecArray | null
    while ((match = regex.exec(content)) !== null) {
      if (match.index > lastIndex) {
        parts.push({ type: 'md', text: content.slice(lastIndex, match.index) })
      }
      if (match[1]) {
        // Tweet marker
        const idMatch = match[1].match(/\/status\/(\d+)/)
        if (idMatch) {
          parts.push({ type: 'tweet', tweetId: idMatch[1] })
        }
      } else if (match[2]) {
        // YouTube marker
        parts.push({ type: 'youtube', videoId: match[2] })
      }
      lastIndex = match.index + match[0].length
    }
    if (lastIndex < content.length) {
      parts.push({ type: 'md', text: content.slice(lastIndex) })
    }
    return parts
  }, [content])

  return (
    <>
      {segments.map((seg, i) =>
        seg.type === 'tweet' ? (
          <TweetEmbed key={i} tweetId={seg.tweetId} />
        ) : seg.type === 'youtube' ? (
          <YouTubeEmbed key={i} videoId={seg.videoId} />
        ) : (
          <MarkdownRenderer key={i} componentOverrides={mdComponentOverrides}>
            {seg.text}
          </MarkdownRenderer>
        ),
      )}
    </>
  )
}

// ── Main component ──────────────────────────────────

/**
 * Renders clipped bookmark content.
 * Auto-detects HTML vs Markdown:
 *   - HTML: rendered with DOMPurify sanitization
 *   - Markdown: rendered with MarkdownRenderer + custom link handling
 *
 * Twitter/X and YouTube URLs are automatically rendered as embed cards.
 */
export default function ClipContentRenderer({ content }: Props) {
  const isHtml = useMemo(() => {
    // Strip tweet/embed markers before checking — they are not HTML content
    const stripped = content.replace(/<!--(?:tweet|youtube):[^>]*-->/g, '').trimStart()
    return stripped.startsWith('<') || /<(?:div|p|h[1-6]|article|section|img|ul|ol|table|blockquote)\b/i.test(stripped)
  }, [content])

  return (
    <>
      <style>{clipStyles}</style>
      {isHtml ? (
        <HtmlRenderer html={content} />
      ) : (
        <MarkdownWithEmbeds content={content} />
      )}
    </>
  )
}

// ── HTML Renderer ───────────────────────────────────

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

    const imgs = containerRef.current.querySelectorAll('img')
    const controllers: AbortController[] = []

    imgs.forEach((img) => {
      const src = img.getAttribute('src')
      if (!src || !src.startsWith('/api/')) return

      const controller = new AbortController()
      controllers.push(controller)

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
          img.dataset.blobUrl = blobUrl
        })
        .catch(() => {
          img.alt = img.alt || '[画像を読み込めません]'
          img.style.minHeight = '2rem'
          img.style.background = ''
        })
    })

    containerRef.current.querySelectorAll('a').forEach((a) => {
      if (a.href && !a.href.startsWith('#')) {
        a.target = '_blank'
        a.rel = 'noopener noreferrer'
      }
    })

    return () => {
      controllers.forEach((c) => c.abort())
      if (containerRef.current) {
        containerRef.current.querySelectorAll('img[data-blob-url]').forEach((img) => {
          const blobUrl = (img as HTMLImageElement).dataset.blobUrl
          if (blobUrl) URL.revokeObjectURL(blobUrl)
        })
      }
    }
  }, [sanitized])

  return (
    <div
      ref={containerRef}
      className="clip-html-content prose prose-sm prose-gray dark:prose-invert max-w-none"
      dangerouslySetInnerHTML={{ __html: sanitized }}
    />
  )
}

// ── Shared styles (applied to both HTML and Markdown modes) ──

const clipStyles = `
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
  display: none;
}
.clip-html-content img[data-blob-url] {
  display: block !important;
}

/* ── Twitter/X embed (react-tweet) ────────────── */
.clip-tweet-wrapper {
  margin: 1rem 0;
}
.clip-tweet-wrapper > div {
  margin: 0 !important;
}

/* ── YouTube embeds ──────────────────────────── */
.clip-youtube-embed {
  margin: 0.75rem 0;
}
.clip-youtube-embed iframe,
.clip-html-content iframe[src*="youtube"],
.clip-html-content iframe[src*="youtu.be"] {
  width: 100%;
  aspect-ratio: 16/9;
  border: none;
  border-radius: 0.5rem;
}
`
