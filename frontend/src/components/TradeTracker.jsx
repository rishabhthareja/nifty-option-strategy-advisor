import { useState, useEffect, useCallback, forwardRef, useImperativeHandle } from 'react'
import { listTrades, getPendingReviews } from '../api'
import TradeRow from './TradeRow'

const STAT_CELLS = [
  { key: 'total_trades', label: 'Total' },
  { key: 'open_trades', label: 'Open' },
  { key: 'wins', label: 'Wins' },
  { key: 'losses', label: 'Losses' },
  { key: 'win_rate_pct', label: 'Win Rate' },
  { key: 'expectancy', label: 'Expectancy' },
]

function fmtStat(key, stats) {
  const v = stats?.[key]
  if (v == null) return '—'
  if (key === 'win_rate_pct') return `${v}%`
  if (key === 'expectancy' || key === 'total_realized_pnl') {
    return `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
  }
  return String(v)
}

const TradeTracker = forwardRef(function TradeTracker(
  { currentStrategy, currentRunId, currentTechnical, currentOI, currentGreeks },
  ref,
) {
  const [trades, setTrades] = useState([])
  const [stats, setStats] = useState(null)
  const [pending, setPending] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [focusTradeId, setFocusTradeId] = useState(null)

  const load = useCallback(async () => {
    try {
      const [listRes, pendingRes] = await Promise.all([
        listTrades(),
        getPendingReviews().catch(() => ({ pending: [], count: 0 })),
      ])
      setTrades(listRes.trades || [])
      setStats(listRes.stats || null)
      setPending(pendingRes.pending || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useImperativeHandle(ref, () => ({
    refresh: () => {
      setLoading(true)
      return load()
    },
  }))

  useEffect(() => {
    load()
    const id = setInterval(load, 60000)
    return () => clearInterval(id)
  }, [load])

  const openTrades = trades.filter((t) => t.status === 'OPEN')
  const closedTrades = trades.filter((t) => t.status === 'CLOSED' || t.status === 'EXPIRED')
  const pendingByTrade = Object.fromEntries(pending.map((p) => [p.trade_id, p]))
  const pendingCount = pending.length
  const firstPending = pending[0]

  return (
    <div className="space-y-4 mt-6">
      <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
        <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase mb-3">
          Trade Journal
        </h2>
        {error && (
          <p className="text-red-400 text-xs mb-2 bg-red-900/20 border border-red-800 rounded p-2">
            {error}
          </p>
        )}
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
          {STAT_CELLS.map(({ key, label }) => (
            <div key={key} className="bg-gray-900 rounded p-2">
              <div className="text-xs text-gray-500">{label}</div>
              <div className="text-sm font-mono text-white">
                {loading ? '…' : fmtStat(key, stats)}
              </div>
            </div>
          ))}
        </div>
      </div>

      {pendingCount > 0 && (
        <div className="bg-amber-900/20 border border-amber-700 rounded-lg p-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-start gap-2">
              <span className="text-amber-400 text-lg">⚠</span>
              <div>
                <p className="text-amber-400 font-bold text-sm">
                  {pendingCount} trade{pendingCount > 1 ? 's' : ''} need your input
                </p>
                <p className="text-amber-300/70 text-xs mt-0.5">
                  Scheduled review is waiting for current premium
                </p>
              </div>
            </div>
            {firstPending && (
              <button
                type="button"
                onClick={() => setFocusTradeId(firstPending.trade_id)}
                className="bg-amber-800 hover:bg-amber-700 text-amber-100 font-bold py-2 px-4 rounded-lg text-sm transition-colors"
              >
                Complete first pending
              </button>
            )}
          </div>
        </div>
      )}

      <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase">
            Open Trades
          </h2>
          <span className="text-xs text-gray-500">{openTrades.length} active</span>
        </div>

        {loading && openTrades.length === 0 && (
          <p className="text-gray-600 text-sm text-center py-4">Loading trades…</p>
        )}

        {!loading && openTrades.length === 0 && (
          <div className="text-center py-8">
            <p className="text-gray-600 text-sm">
              No open trades. Run an analysis and click
              <span className="text-blue-400"> Track as Paper Trade</span> to start.
            </p>
          </div>
        )}

        {openTrades.map((t) => (
          <TradeRow
            key={t.trade_id}
            trade={t}
            pendingReview={pendingByTrade[t.trade_id]}
            onRefresh={load}
            autoExpand={focusTradeId === t.trade_id}
            onFocusPending={() => setFocusTradeId(t.trade_id)}
          />
        ))}
      </div>

      {closedTrades.length > 0 && (
        <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 space-y-3">
          <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase">
            Closed Trades
          </h2>
          {closedTrades.map((t) => (
            <TradeRow key={t.trade_id} trade={t} onRefresh={load} />
          ))}
        </div>
      )}

      {/* Props reserved for future quick-open from last run */}
      {currentStrategy && currentRunId && (
        <p className="text-center text-gray-700 text-xs">
          Last run {currentRunId} · {currentStrategy.strategy}
          {currentOI?.pcr != null && ` · PCR ${currentOI.pcr}`}
        </p>
      )}
    </div>
  )
})

export default TradeTracker
