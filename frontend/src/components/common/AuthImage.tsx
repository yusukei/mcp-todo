import { useEffect, useState } from 'react'
import { api } from '../../api/client'

interface Props extends React.ImgHTMLAttributes<HTMLImageElement> {
  src?: string
  onLoadError?: () => void
}

/**
 * An <img> replacement that fetches the image via authenticated axios
 * and renders it as a blob URL. This allows images behind JWT-protected
 * endpoints to be displayed in the browser.
 *
 * Falls back to a regular <img> for external URLs.
 */
export default function AuthImage({ src, alt, onLoadError, ...rest }: Props) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [error, setError] = useState(false)

  const isInternal = src && src.startsWith('/api/')

  useEffect(() => {
    if (!src || !isInternal) return

    let cancelled = false
    const controller = new AbortController()

    api
      .get(src.replace('/api/v1', ''), {
        responseType: 'blob',
        signal: controller.signal,
      })
      .then((res) => {
        if (!cancelled) {
          const url = URL.createObjectURL(res.data)
          setBlobUrl(url)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true)
          onLoadError?.()
        }
      })

    return () => {
      cancelled = true
      controller.abort()
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src])

  if (!src) return null

  // External URLs — render directly
  if (!isInternal) return <img src={src} alt={alt} {...rest} />

  if (error) return <span className="inline-block w-full max-w-xs h-24 bg-gray-100 dark:bg-gray-800 rounded flex items-center justify-center text-xs text-gray-400">[画像を読み込めません]</span>
  if (!blobUrl) return <span className="inline-block w-full max-w-xs h-32 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />

  return <img src={blobUrl} alt={alt} {...rest} />
}
