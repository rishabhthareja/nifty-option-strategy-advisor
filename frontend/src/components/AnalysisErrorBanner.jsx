import { useState } from 'react'

export default function AnalysisErrorBanner({ error }) {
  const [showDetail, setShowDetail] = useState(false)

  if (!error) return null

  const isRateLimit = error.code === 'gemini_rate_limit'

  return (
    <div className="bg-red-900/20 border border-red-700 rounded-lg p-4 text-sm">
      <div className="flex items-start gap-3">
        <span className="text-xl shrink-0" aria-hidden>
          {isRateLimit ? '\u23F3' : '\u26A0\uFE0F'}
        </span>
        <div className="flex-1 space-y-2">
          <p className="text-red-300 font-semibold">
            {isRateLimit ? 'Gemini rate limit' : 'Analysis failed'}
          </p>
          <p className="text-red-200/90">{error.message}</p>
          {error.hint && (
            <p className="text-gray-400 text-xs leading-relaxed">{error.hint}</p>
          )}
          {isRateLimit && error.links && (
            <p className="text-xs">
              <a
                href={error.links.rate_limits}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 hover:underline"
              >
                View rate limits
              </a>
              <span className="text-gray-600 mx-2">&middot;</span>
              <a
                href={error.links.billing}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 hover:underline"
              >
                Google AI Studio (billing)
              </a>
            </p>
          )}
          {error.detail && (
            <div>
              <button
                type="button"
                onClick={() => setShowDetail((v) => !v)}
                className="text-xs text-gray-500 hover:text-gray-300 underline"
              >
                {showDetail ? 'Hide technical details' : 'Show technical details'}
              </button>
              {showDetail && (
                <pre className="mt-2 text-xs text-gray-500 whitespace-pre-wrap break-all max-h-40 overflow-auto bg-black/30 rounded p-2">
                  {error.detail}
                </pre>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
