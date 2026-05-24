// Dev: Vite proxies /api → backend (avoids CORS). Override with VITE_BACKEND_URL if needed.
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || (import.meta.env.DEV ? '/api' : 'http://127.0.0.1:8000')

async function errorFromResponse(res, fallback) {
  try {
    const body = await res.json()
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
