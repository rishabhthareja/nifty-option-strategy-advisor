import { useState } from 'react'
import { approveOrder, executeOrder, openTrade } from '../api'
import { expirySummary } from '../utils/formatExpiry'
import { buildPaperTradeOpenPayload } from '../utils/tradeOpenPayload'

export default function HITLPanel({
  runId,
  strategy,
  evaluation,
  onDecision,
  onPaperTradeOpened,
  market,
  oi,
  greeks,
  technical,
}) {
  const [decision, setDecision] = useState(null) // null | 'approved' | 'rejected'
  const [rejectReason, setRejectReason] = useState('')
  const [showRejectInput, setShowRejectInput] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [execResult, setExecResult] = useState(null)
  const [error, setError] = useState(null)
  const [trackingPaper, setTrackingPaper] = useState(false)
  const [paperTradeResult, setPaperTradeResult] = useState(null)
  const [paperTradeError, setPaperTradeError] = useState(null)

  if (!runId || !strategy || strategy.strategy === 'WAIT') return null
  if (strategy.integrity_blocked || strategy.data_trade_ready === false) return (
    <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 text-center">
      <p className="text-red-400 font-bold">Trade blocked — live data required.</p>
      <p className="text-red-300/80 text-sm mt-1">{strategy.integrity_note || 'Quotes and option chain must be live before approval.'}</p>
    </div>
  )
  if (evaluation?.status === 'skipped') {
    return (
      <div className="bg-gray-900/40 border border-gray-700 rounded-lg p-4 text-center">
        <p className="text-gray-400 font-bold">Evaluator skipped (pre-flight WAIT).</p>
        <p className="text-gray-500 text-sm mt-1">No orders — entry guardrails blocked the trade.</p>
      </div>
    )
  }
  if (!evaluation?.is_validated) return (
    <div className="bg-yellow-900/20 border border-yellow-800 rounded-lg p-4 text-center">
      <p className="text-yellow-400 font-bold">Strategy not validated by evaluator (score too low).</p>
      <p className="text-yellow-600 text-sm mt-1">No orders will be placed.</p>
    </div>
  )

  const handleApprove = async () => {
    try {
      await approveOrder(runId, true, '')
      setExecuting(true)
      const result = await executeOrder(runId)
      setExecResult(result)
      setDecision('approved')
      onDecision?.('approved')
    } catch (e) {
      setError(e.message)
    } finally {
      setExecuting(false)
    }
  }

  const handleReject = async () => {
    if (!showRejectInput) { setShowRejectInput(true); return }
    try {
      await approveOrder(runId, false, rejectReason)
      setDecision('rejected')
      onDecision?.('rejected')
    } catch (e) {
      setError(e.message)
    }
  }

  const handleTrackPaper = async () => {
    setTrackingPaper(true)
    setPaperTradeError(null)
    setPaperTradeResult(null)
    try {
      const payload = buildPaperTradeOpenPayload({
        strategy,
        runId,
        oi,
        greeks,
        market,
        technical,
      })
      const res = await openTrade(payload)
      setPaperTradeResult(res)
      await onPaperTradeOpened?.()
    } catch (e) {
      setPaperTradeError(e.message)
    } finally {
      setTrackingPaper(false)
    }
  }

  const showTrackPaper =
    decision == null &&
    strategy?.strategy !== 'WAIT' &&
    !strategy?.integrity_blocked &&
    strategy?.data_trade_ready !== false

  if (decision === 'approved' && execResult) return (
    <div className="bg-green-900/20 border border-green-700 rounded-lg p-4 text-center">
      <div className="text-green-400 text-2xl mb-2">✓</div>
      <p className="text-green-400 font-bold text-lg">Orders Placed Successfully</p>
      <p className="text-green-600 text-sm mt-1">{execResult.timestamp}</p>
      <p className="text-gray-400 text-xs mt-2">{execResult.orders?.length} order leg(s) executed</p>
    </div>
  )

  if (decision === 'rejected') return (
    <div className="bg-red-900/20 border border-red-700 rounded-lg p-4 text-center">
      <div className="text-red-400 text-2xl mb-2">✗</div>
      <p className="text-red-400 font-bold">Trade Rejected</p>
      {rejectReason && <p className="text-gray-400 text-sm mt-1">Reason: {rejectReason}</p>}
    </div>
  )

  return (
    <div className="bg-[#1a1a1a] border-2 border-yellow-700 rounded-lg p-5 space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-yellow-400 text-xl">⚠</span>
        <h2 className="text-yellow-400 font-bold text-lg">Human Approval Required</h2>
      </div>
      <p className="text-gray-400 text-sm">
        The following strategy is ready for review. Backend live order execution is disabled unless
        <strong className="text-white"> ENABLE_LIVE_ORDERS=true</strong> is set. Review carefully before approving.
      </p>

      <div className="bg-gray-900 rounded-lg p-4 space-y-1 text-sm">
        <div className="flex justify-between">
          <span className="text-gray-400">Strategy</span>
          <span className="text-blue-400 font-bold">{strategy.strategy.replace('_', ' ')}</span>
        </div>
        {(() => {
          const exp = expirySummary(strategy)
          if (!exp) return null
          return (
            <div className="flex justify-between">
              <span className="text-gray-400">Option expiry</span>
              <span className="text-cyan-400 font-mono text-right">
                {exp.primary}
                {exp.dtePart != null && <span className="text-gray-500 ml-1">({exp.dtePart})</span>}
              </span>
            </div>
          )
        })()}
        <div className="flex justify-between">
          <span className="text-gray-400">Net Premium (LTP)</span>
          <span className="text-green-400 font-bold">{strategy.net_premium?.toFixed(2) ?? '—'} pts</span>
        </div>
        {strategy.conservative_net_premium != null && (
          <div className="flex justify-between">
            <span className="text-gray-400">Net Premium (conservative)</span>
            <span className="text-amber-400 font-bold">{strategy.conservative_net_premium.toFixed(2)} pts</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-gray-400">Max Profit (LTP)</span>
          <span className="text-green-400">₹{strategy.max_profit?.toLocaleString('en-IN') ?? '—'}</span>
        </div>
        {strategy.conservative_max_profit != null && (
          <div className="flex justify-between">
            <span className="text-gray-400">Max Profit (conservative)</span>
            <span className="text-amber-400">₹{strategy.conservative_max_profit.toLocaleString('en-IN')}</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-gray-400">Max Loss (LTP)</span>
          <span className="text-red-400">₹{strategy.max_loss?.toLocaleString('en-IN') ?? 'Unlimited'}</span>
        </div>
        {strategy.conservative_max_loss != null && (
          <div className="flex justify-between">
            <span className="text-gray-400">Max Loss (conservative)</span>
            <span className="text-amber-400">₹{strategy.conservative_max_loss.toLocaleString('en-IN')}</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-gray-400">Evaluator Score</span>
          <span className="text-yellow-400 font-bold">{evaluation.quality_score.toFixed(1)}/10</span>
        </div>
      </div>

      {error && <p className="text-red-400 text-sm bg-red-900/20 rounded p-2">{error}</p>}

      {showRejectInput && (
        <textarea
          className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
          rows={2}
          placeholder="Reason for rejection (optional)..."
          value={rejectReason}
          onChange={e => setRejectReason(e.target.value)}
        />
      )}

      <div className="flex gap-4">
        <button
          onClick={handleApprove}
          disabled={executing}
          className="flex-1 bg-green-700 hover:bg-green-600 disabled:bg-gray-700 text-white font-bold py-3 px-6 rounded-lg text-lg transition-colors"
        >
          {executing ? 'Placing Orders...' : '✓ APPROVE & EXECUTE'}
        </button>
        <button
          onClick={handleReject}
          className="flex-1 bg-red-700 hover:bg-red-600 text-white font-bold py-3 px-6 rounded-lg text-lg transition-colors"
        >
          ✗ {showRejectInput ? 'CONFIRM REJECT' : 'REJECT'}
        </button>
      </div>

      {showTrackPaper && (
        <div className="border-t border-gray-700 pt-3">
          <p className="text-xs text-gray-500 mb-2">
            Not executing live? Track this setup as a paper trade.
          </p>
          <button
            type="button"
            onClick={handleTrackPaper}
            disabled={trackingPaper}
            className="w-full bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 text-gray-300 font-bold py-2 px-4 rounded-lg text-sm transition-colors"
          >
            {trackingPaper ? 'Opening paper trade...' : '📋 Track as Paper Trade'}
          </button>
          {paperTradeResult && (
            <p className="text-green-400 text-xs mt-1">
              Paper trade opened — ID: {paperTradeResult.trade_id}
              {paperTradeResult.warning && (
                <span className="text-amber-400 block">{paperTradeResult.warning}</span>
              )}
            </p>
          )}
          {paperTradeError && (
            <p className="text-red-400 text-xs mt-1">{paperTradeError}</p>
          )}
        </div>
      )}
    </div>
  )
}
