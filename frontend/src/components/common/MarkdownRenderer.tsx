import Markdown from 'react-markdown'

interface Props {
  children: string
  className?: string
}

/**
 * Renders Markdown content safely using react-markdown.
 *
 * XSS safety: react-markdown does not render raw HTML by default,
 * so no additional sanitization is needed.
 *
 * Links open in a new tab with rel="noopener noreferrer" for security.
 */
export default function MarkdownRenderer({ children, className }: Props) {
  return (
    <div className={className ?? 'prose prose-sm prose-gray dark:prose-invert max-w-none'}>
      <Markdown
        components={{
          a: ({ href, children: linkChildren }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300 underline"
            >
              {linkChildren}
            </a>
          ),
        }}
      >
        {children}
      </Markdown>
    </div>
  )
}
