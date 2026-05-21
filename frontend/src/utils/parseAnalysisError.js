/** Normalize SSE error events (structured or legacy string-only). */
export function parseAnalysisError(event) {
  if (!event) return null

  if (event.code) {
    return {
      code: event.code,
      message: event.message || 'Analysis failed.',
      hint: event.hint || null,
      detail: event.detail || null,
      links: event.links || null,
      retry_seconds: event.retry_seconds ?? null,
    }
  }

  const raw = typeof event.message === 'string' ? event.message : String(event)
  const lower = raw.toLowerCase()

  if (
    lower.includes('resource_exhausted') ||
    lower.includes('429') ||
    lower.includes('quota') ||
    lower.includes('rate limit')
  ) {
    const retryMatch = raw.match(/retry in ([\d.]+)s/i)
    const retrySeconds = retryMatch ? Math.max(parseInt(retryMatch[1], 10), 1) : 60
    return {
      code: 'gemini_rate_limit',
      message: `Gemini API rate limit reached. Wait about ${retrySeconds} seconds, then run analysis again.`,
      hint:
        'Free tier allows very few requests per minute. Enable billing in Google AI Studio, or change GEMINI_MODEL in backend/.env.',
      detail: raw.length > 200 ? raw : null,
      links: {
        rate_limits: 'https://ai.dev/rate-limit',
        billing: 'https://aistudio.google.com/',
      },
      retry_seconds: retrySeconds,
    }
  }

  return {
    code: 'analysis_failed',
    message: raw.length > 400 ? `${raw.slice(0, 397)}...` : raw,
    hint: null,
    detail: raw.length > 400 ? raw : null,
    links: null,
    retry_seconds: null,
  }
}
