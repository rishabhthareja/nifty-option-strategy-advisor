const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'

async function errorFromResponse(res, fallback) {
  try {
    const body = await res.json()
    return body.detail || body.message || fallback
  } catch {
    return fallback
  }
}

export async function getLiveData() {
  const res = await fetch(`${BACKEND_URL}/live-data`)
  if (!res.ok) throw new Error(await errorFromResponse(res, 'Failed to fetch live data'))
  return res.json()
}

export function startAnalysis(onAgentUpdate) {
  const es = new EventSource(`${BACKEND_URL}/analyse/stream`)
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      onAgentUpdate(data)
    } catch {}
  }
  es.onerror = () => es.close()
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
