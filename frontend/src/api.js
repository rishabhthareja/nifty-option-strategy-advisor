// Dev: Vite proxies /api → backend (avoids CORS). Override with VITE_BACKEND_URL if needed.
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || (import.meta.env.DEV ? '/api' : 'http://127.0.0.1:8000')

async function errorFromResponse(res, fallback) {
  try {
    const body = await res.json()
    if (typeof body.detail === 'object') return JSON.stringify(body.detail)
    return body.detail || body.message || fallback
  } catch {
    return fallback
  }
}

export async function getLiveData() {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 12000)
  try {
    const res = await fetch(`${BACKEND_URL}/live-data`, { signal: controller.signal })
    if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to fetch live data'))
    return res.json()
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error('Live data timed out — backend may be busy. Retry in a few seconds.')
    }
    if (err?.message === 'Failed to fetch') {
      throw new Error('Cannot reach backend — start uvicorn on port 8000, then refresh.')
    }
    throw err
  } finally {
    clearTimeout(timeoutId)
  }
}

export function startAnalysis(onAgentUpdate) {
  const es = new EventSource(`${BACKEND_URL}/analyse/stream`)
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      onAgentUpdate(data)
    } catch {}
  }
  es.onerror = () => {
    onAgentUpdate({
      agent: 'error',
      detail: 'Lost connection to the analysis stream. Ensure the backend is running (port 8000) and OpenAlgo is reachable.',
    })
    es.close()
  }
  return es
}

export async function approveOrder(runId, approved, reason = '') {
  const res = await fetch(`${BACKEND_URL}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, approved, reason }),
  })
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Approval request failed'))
  return res.json()
}

export async function executeOrder(runId) {
  const res = await fetch(`${BACKEND_URL}/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId }),
  })
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Execution failed'))
  return res.json()
}

export async function getHistory() {
  const res = await fetch(`${BACKEND_URL}/history`)
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to fetch history'))
  return res.json()
}

// ── Trade tracking ───────────────────────────────────────────────────────────

export async function openTrade(data) {
  const res = await fetch(`${BACKEND_URL}/trade/open`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to open trade'))
  return res.json()
}

export async function listTrades(params = {}) {
  const q = new URLSearchParams()
  if (params.trade_type) q.set('trade_type', params.trade_type)
  if (params.status) q.set('status', params.status)
  const qs = q.toString()
  const res = await fetch(`${BACKEND_URL}/trade/list${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to list trades'))
  return res.json()
}

export async function getTradeStats(tradeType = null) {
  const qs = tradeType ? `?trade_type=${tradeType}` : ''
  const res = await fetch(`${BACKEND_URL}/trade/stats${qs}`)
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to fetch trade stats'))
  return res.json()
}

export async function getTrade(tradeId) {
  const res = await fetch(`${BACKEND_URL}/trade/${tradeId}`)
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to fetch trade'))
  return res.json()
}

export async function markTrade(tradeId, body) {
  const res = await fetch(`${BACKEND_URL}/trade/${tradeId}/mark`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to mark trade'))
  return res.json()
}

export async function closeTrade(tradeId, body) {
  const res = await fetch(`${BACKEND_URL}/trade/${tradeId}/close`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to close trade'))
  return res.json()
}

export async function getPendingReviews() {
  const res = await fetch(`${BACKEND_URL}/trade/reviews/pending`)
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to fetch pending reviews'))
  return res.json()
}

export async function getLatestReview(tradeId) {
  const res = await fetch(`${BACKEND_URL}/trade/${tradeId}/reviews/latest`)
  if (!res.ok) {
    if (res.status === 404) return null
    throw new Error(await errorFromResponse(res, 'Failed to fetch latest review'))
  }
  return res.json()
}

export async function getTradeReviews(tradeId) {
  const res = await fetch(`${BACKEND_URL}/trade/${tradeId}/reviews`)
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to fetch reviews'))
  return res.json()
}

export async function getReviewSummary(tradeId) {
  const res = await fetch(`${BACKEND_URL}/trade/${tradeId}/review-summary`)
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to fetch review summary'))
  return res.json()
}

export async function completePendingReview(tradeId, body) {
  const res = await fetch(`${BACKEND_URL}/trade/${tradeId}/reviews/pending/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to complete pending review'))
  return res.json()
}

export async function triggerReviewNow(checkSlot, tradeId = null) {
  const res = await fetch(`${BACKEND_URL}/trade/review-now`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ check_slot: checkSlot, trade_id: tradeId }),
  })
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to trigger review'))
  return res.json()
}
