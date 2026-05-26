import { useState } from 'react'
import { closeTrade } from '../api'
import { legDescription } from '../utils/tradeOpenPayload'

function fmtMoney(v) {
  if (v == null || Number.isNaN(v)) return '—'
  const n = Number(v)
  const sign = n >= 0 ? '+' : ''
  return `${sign}₹${Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function signalStyle(review) {
  if (!review) return 'border-gray-700 text-gray-400 bg-gray-900/40'
  if (review.exit_signal === 'HARD_EXIT') return 'border-red-700 text-red-400 bg-red-900/40'
  if (review.exit_signal === 'SOFT_EXIT') return 'border-amber-700 text-amber-400 bg-amber-900/40'
  return 'border-green-700 text-green-400 bg-green-900/40'
}

function signalLabel(review) {
  if (!review) return 'Review pending'
  if (review.exit_signal === 'HARD_EXIT') return 'HARD EXIT'
  if (review.exit_signal === 'SOFT_EXIT') return 'SOFT EXIT'
  return 'HOLD'
}

function TradeMonitorCard({ item, onClosed }) {
  const { trade, review, current_premium, unrealized_pnl, error } = item
  const [closing, setClosing] = useState(false)
  const [closeError, setCloseError] = useState(null)
  const [showClose, setShowClose] = useState(false)
  const [exitPremium, setExitPremium] = useState(
    current_premium != null ? String(current_premium) : '',
  )

  const dte = review?.dte_remaining ?? trade?.dte_at_entry
  const pnlPct = review?.pnl_pct_of_max_profit
  const actionCategory = review?.action_category
  const evidence = review?.evidence_list || []

  const handleClose = async () => {
    const prem = parseFloat(exitPremium)
    if (!prem && prem !== 0) {
      setCloseError('Enter exit premium')
      return
    }
    setClosing(true)
    setCloseError(null)
    try {
      await closeTrade(trade.trade_id, {
        exit_premium: prem,
        exit_spot: review?.current_spot ?? trade.entry_spot,
        exit_reason: review?.recommended_action || 'user_manual',
        exit_triggered_by: 'user_manual',
      })
      setShowClose(false)
      onClosed?.()
    } catch (e) {
      setCloseError(e.message)
    } finally {
      setClosing(false)
    }
  }

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-bold text-white">
            {trade.strategy} · LIVE · {trade.trade_id}
          </div>
          <div className="text-xs text-gray-500 mt-0.5">{legDescription(trade)}</div>
          <div className="text-xs text-gray-500">Expiry {trade.expiry_date} · DTE {dte ?? '—'}</div>
        </div>
        <span className={`text-xs font-bold px-2 py-1 rounded border ${signalStyle(review)}`}>
          {signalLabel(review)}
        </span>
      </div>

      {error && (
        <div className="text-xs text-red-400 border border-red-800 rounded p-2">{error}</div>
      )}

      <div className="grid grid-cols-3 gap-3 text-xs">
        <div className="bg-gray-950 rounded p-2">
          <div className="text-gray-500">Close premium</div>
          <div className="text-white font-mono text-sm">
            {current_premium != null ? `₹${current_premium}` : '—'}
          </div>
        </div>
        <div className="bg-gray-950 rounded p-2">
          <div className="text-gray-500">Unrealized P&L</div>
          <div
            className={`font-mono text-sm ${
              unrealized_pnl > 0 ? 'text-green-400' : unrealized_pnl < 0 ? 'text-red-400' : 'text-white'
            }`}
          >
            {fmtMoney(unrealized_pnl)}
          </div>
        </div>
        <div className="bg-gray-950 rounded p-2">
          <div className="text-gray-500">% max profit</div>
          <div className="text-white font-mono text-sm">
            {pnlPct != null ? `${pnlPct}%` : '—'}
          </div>
        </div>
      </div>

      {actionCategory && (
        <div className="text-xs">
          <span className="text-gray-500">Action: </span>
          <span className="text-blue-300 font-mono">{actionCategory}</span>
          {review?.recommended_action && (
            <span className="text-gray-500"> → {review.recommended_action}</span>
          )}
        </div>
      )}

      {review?.reasoning && (
        <p className="text-xs text-gray-400 leading-relaxed">{review.reasoning}</p>
      )}

      {evidence.length > 0 && (
        <ul className="text-xs text-gray-500 space-y-1 list-disc list-inside">
          {evidence.map((ev, i) => (
            <li key={i}>{typeof ev === 'string' ? ev : JSON.stringify(ev)}</li>
          ))}
        </ul>
      )}

      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={() => setShowClose(false)}
          className="px-3 py-1.5 rounded text-xs bg-gray-800 hover:bg-gray-700 text-gray-300"
        >
          Hold
        </button>
        <button
          type="button"
          onClick={() => setShowClose(true)}
          className="px-3 py-1.5 rounded text-xs bg-red-900/60 hover:bg-red-800 text-red-200 border border-red-800"
        >
          Close
        </button>
      </div>

      {showClose && (
        <div className="border border-gray-700 rounded p-3 space-y-2 bg-gray-950">
          <label className="text-xs text-gray-500 block">Exit premium (debit to close)</label>
          <input
            type="number"
            step="0.05"
            value={exitPremium}
            onChange={(e) => setExitPremium(e.target.value)}
            className="w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-white font-mono"
          />
          {closeError && <p className="text-xs text-red-400">{closeError}</p>}
          <button
            type="button"
            disabled={closing}
            onClick={handleClose}
            className="px-3 py-1.5 rounded text-xs bg-red-700 hover:bg-red-600 text-white disabled:opacity-50"
          >
            {closing ? 'Closing…' : 'Confirm close'}
          </button>
        </div>
      )}
    </div>
  )
}

export default function PositionMonitorPanel({ results = [], onTradeClosed }) {
  if (!results.length) {
    return (
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 text-center text-gray-500 text-sm">
        No open live positions to monitor.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-bold text-gray-400 uppercase tracking-widest">
        Position Monitor
      </h2>
      {results.map((item, idx) => (
        <TradeMonitorCard
          key={item.trade?.trade_id || idx}
          item={item}
          onClosed={onTradeClosed}
        />
      ))}
    </div>
  )
}
