import { useCallback, useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import mermaid from 'mermaid'
import type { Components } from 'react-markdown'

mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'strict',
})

let mermaidIdCounter = 0

function isDarkMode() {
  return document.documentElement.classList.contains('dark')
}

function MermaidBlock({ code }: { code: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState<string>('')
  const [error, setError] = useState<string>('')

  useEffect(() => {
    let cancelled = false
    const id = `mermaid-${++mermaidIdCounter}`
    mermaid.initialize({
      startOnLoad: false,
      theme: isDarkMode() ? 'dark' : 'default',
      securityLevel: 'strict',
    })
    mermaid
      .render(id, code)
      .then(({ svg: rendered }) => {
        if (!cancelled) setSvg(rendered)
      })
      .catch((err) => {
        if (!cancelled) setError(String(err))
      })
    return () => {
      cancelled = true
    }
  }, [code])

  if (error) {
    return (
      <pre className="text-red-600 bg-red-50 dark:bg-red-900/20 dark:text-red-400 p-3 rounded text-xs overflow-auto">
        {error}
      </pre>
    )
  }

  return (
    <div
      ref={containerRef}
      className="my-4 flex justify-center overflow-auto"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  )
}

interface Props {
  children: string
  className?: string
  componentOverrides?: Partial<Components>
}

/**
 * Renders Markdown content safely using react-markdown.
 *
 * XSS safety: react-markdown does not render raw HTML by default,
 * so no additional sanitization is needed.
 *
 * Links open in a new tab with rel="noopener noreferrer" for security.
 *
 * Mermaid code blocks (```mermaid) are rendered as diagrams via the mermaid library.
 * mermaid.initialize uses securityLevel: 'strict' which disables HTML labels and click events.
 */
export default function MarkdownRenderer({ children, className, componentOverrides }: Props) {
  const codeComponent: Components['code'] = useCallback(
    ({ className: codeClassName, children: codeChildren, ...rest }: any) => {
      const match = /language-(\w+)/.exec(codeClassName || '')
      if (match && match[1] === 'mermaid') {
        const code = String(codeChildren).replace(/\n$/, '')
        return <MermaidBlock code={code} />
      }
      return (
        <code className={codeClassName} {...rest}>
          {codeChildren}
        </code>
      )
    },
    [],
  )

  return (
    <div className={className ?? 'prose prose-sm prose-gray dark:prose-invert max-w-none'}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: linkChildren }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent-600 hover:text-accent-800 dark:text-accent-400 dark:hover:text-accent-300 underline"
            >
              {linkChildren}
            </a>
          ),
          pre: ({ children: preChildren, ...preRest }: any) => {
            // Unwrap <pre> for mermaid blocks (MermaidBlock handles its own wrapper)
            const child = Array.isArray(preChildren) ? preChildren[0] : preChildren
            if (child?.props?.className && /language-mermaid/.test(child.props.className)) {
              return <>{preChildren}</>
            }
            return <pre {...preRest}>{preChildren}</pre>
          },
          code: codeComponent,
          ...componentOverrides,
        }}
      >
        {children.replace(/\\n/g, '\n')}
      </Markdown>
    </div>
  )
}
