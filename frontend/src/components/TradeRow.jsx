import { useState, useEffect } from 'react'
import {
  getTrade,
  getLatestReview,
  getReviewSummary,
  markTrade,
  closeTrade,
  completePendingReview,
} from '../api'
import { legDescription, previewPnl } from '../utils/tradeOpenPayload'

function fmtMoney(v) {
  if (v == null || Number.isNaN(v)) return '—'
  const n = Number(v)
  const sign = n >= 0 ? '+' : ''
  return `${sign}₹${Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

function fmtVal(v, unit = '') {
  if (v == null || v === '') return '—'
  return `${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}${unit}`
}

function CompareRow({ label, entry, today, unit = '', showPct, changePct }) {
  const worse =
    changePct != null && Math.abs(changePct) > 40
      ? 'warn'
      : today != null && entry != null && Math.abs(today - entry) > (unit === 'pts' ? 50 : 0.15)
        ? 'warn'
        : 'ok'

  const icon = worse === 'warn' ? '⚠' : '✓'
  const iconColor = worse === 'warn' ? 'text-amber-400' : 'text-green-400'

  return (
    <div className="flex justify-between items-center text-xs py-1 border-b border-gray-800/80 last:border-0">
      <span className="text-gray-500 w-24 shrink-0">{label}</span>
      <span className="text-gray-400 font-mono">{fmtVal(entry, unit)}</span>
      <span className="text-gray-600">→</span>
      <span className="text-white font-mono">{fmtVal(today, unit)}</span>
      {showPct && changePct != null && (
        <span className={`font-mono ${changePct > 0 ? 'text-amber-400' : 'text-gray-500'}`}>
          ({changePct > 0 ? '+' : ''}{changePct}%)
        </span>
      )}
      <span className={`${iconColor} ml-1`}>{icon}</span>
    </div>
  )
}

function signalBadge(review) {
  if (!review) return { label: 'No review yet', cls: 'border-gray-700 text-gray-500 bg-gray-900/40' }
  if (review.review_status === 'PENDING_INPUT') {
    return { label: 'Input needed', cls: 'border-amber-700 text-amber-400 bg-amber-900/40' }
  }
  if (review.exit_signal === 'HARD_EXIT') {
    return { label: 'HARD EXIT', cls: 'border-red-700 text-red-400 bg-red-900/40' }
  }
  if (review.exit_signal === 'SOFT_EXIT') {
    return { label: 'SOFT EXIT', cls: 'border-amber-700 text-amber-400 bg-amber-900/40' }
  }
  return { label: 'HOLD', cls: 'border-green-700 text-green-400 bg-green-900/40' }
}

export default function TradeRow({ trade, pendingReview, onRefresh, onFocusPending, autoExpand }) {
  const [expanded, setExpanded] = useState(!!autoExpand)
  const [detail, setDetail] = useState(null)
  const [review, setReview] = useState(null)
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)

  const [showMarkInput, setShowMarkInput] = useState(false)
  const [markMode, setMarkMode] = useState('simple')
  const [markPremium, setMarkPremium] = useState('')
  const [markNote, setMarkNote] = useState('')
  const [snapshotJson, setSnapshotJson] = useState('')
  const [jsonError, setJsonError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [markResult, setMarkResult] = useState(null)

  const [showClose, setShowClose] = useState(false)
  const [exitPremium, setExitPremium] = useState('')
  const [exitReason, setExitReason] = useState('manual')
  const [closing, setClosing] = useState(false)
  const [closeError, setCloseError] = useState(null)

  const latestReview = pendingReview || review
  const badge = signalBadge(latestReview)
  const marks = detail?.marks || []
  const lastMark = marks.length ? marks[marks.length - 1] : null
  const displayPnl = lastMark?.unrealized_pnl ?? latestReview?.unrealized_pnl
  const displayPnlPct = lastMark?.pnl_pct_of_max_profit ?? latestReview?.pnl_pct_of_max_profit

  useEffect(() => {
    if (!expanded || detail) return
    let cancelled = false
    setLoading(true)
    Promise.all([
      getTrade(trade.trade_id),
      getLatestReview(trade.trade_id).catch(() => null),
      getReviewSummary(trade.trade_id).catch(() => null),
    ])
      .then(([t, r, s]) => {
        if (cancelled) return
        setDetail(t)
        const rev = r?.review ?? null
        setReview(rev)
        setSummary(s)
        if (rev?.review_status === 'PENDING_INPUT') {
          setShowMarkInput(true)
          setMarkMode('sensibull')
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [expanded, trade.trade_id, detail])

  const reloadExpanded = async () => {
    const [t, r, s] = await Promise.all([
      getTrade(trade.trade_id),
      getLatestReview(trade.trade_id).catch(() => null),
      getReviewSummary(trade.trade_id).catch(() => null),
    ])
    setDetail(t)
    setReview(r?.review ?? null)
    setSummary(s)
    onRefresh?.()
  }

  const parseSnapshot = () => {
    try {
      const raw = JSON.parse(snapshotJson)
      const spot = raw.spot ?? raw.underlying_price
      const legs = raw.legs || []
      const summaryObj = raw.summary || {}
      const net =
        summaryObj.net_premium_to_close ??
        summaryObj.net_premium ??
        raw.net_premium_to_close
      if (spot == null || !legs.length || net == null) {
        throw new Error('JSON must include spot, legs[], and summary.net_premium_to_close')
      }
      return {
        snapshot_time: raw.snapshot_time || new Date().toISOString(),
        spot: Number(spot),
        legs: legs.map((lg) => ({
          strike: Number(lg.strike),
          type: lg.type === 'PE' || lg.type === 'put' ? 'PE' : 'CE',
          action: lg.action?.toUpperCase() === 'BUY' ? 'BUY' : 'SELL',
          ltp: Number(lg.ltp ?? lg.price ?? 0),
          delta: lg.delta,
          theta: lg.theta,
          gamma: lg.gamma,
          vega: lg.vega,
          iv: lg.iv,
        })),
        summary: { net_premium_to_close: Number(net) },
      }
    } catch (e) {
      throw new Error(e.message || 'Invalid JSON')
    }
  }

  const handleSubmitMark = async () => {
    setSubmitting(true)
    setMarkResult(null)
    setJsonError(null)
    setCloseError(null)
    try {
      const pending =
        review?.review_status === 'PENDING_INPUT' ||
        latestReview?.review_status === 'PENDING_INPUT'

      if (markMode === 'sensibull') {
        const snap = parseSnapshot()
        if (pending) {
          const res = await completePendingReview(trade.trade_id, {
            sensibull_snapshot: snap,
            user_note: markNote || undefined,
          })
          setMarkResult({
            exit_alert: res.exit_signal === 'HARD_EXIT' ? 'HARD' : res.exit_signal === 'SOFT_EXIT' ? 'SOFT' : 'NONE',
            unrealized_pnl: res.review?.unrealized_pnl,
            alert_detail: res.reasoning,
          })
        } else {
          const res = await markTrade(trade.trade_id, {
            current_premium: snap.summary.net_premium_to_close,
            spot: snap.spot,
            data_source: 'manual',
            user_note: markNote || undefined,
          })
          if (res.needs_user_input) {
            setMarkResult({ exit_alert: 'PENDING', alert_detail: res.user_prompt })
          } else {
            setMarkResult(res)
          }
        }
      } else {
        const prem = parseFloat(markPremium)
        if (Number.isNaN(prem)) throw new Error('Enter a valid premium')
        if (pending) {
          const res = await completePendingReview(trade.trade_id, {
            current_premium: prem,
            user_note: markNote || undefined,
          })
          setMarkResult({
            exit_alert: res.exit_signal === 'HARD_EXIT' ? 'HARD' : res.exit_signal === 'SOFT_EXIT' ? 'SOFT' : 'NONE',
            unrealized_pnl: res.review?.unrealized_pnl,
            alert_detail: res.reasoning,
          })
        } else {
          const res = await markTrade(trade.trade_id, {
            current_premium: prem,
            data_source: 'manual',
            user_note: markNote || undefined,
          })
          setMarkResult(res)
        }
      }
      setMarkPremium('')
      setSnapshotJson('')
      await reloadExpanded()
    } catch (e) {
      setJsonError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmClose = async () => {
    const prem = parseFloat(exitPremium)
    if (Number.isNaN(prem)) return
    setClosing(true)
    setCloseError(null)
    try {
      await closeTrade(trade.trade_id, {
        exit_premium: prem,
        exit_spot: review?.current_spot ?? trade.entry_spot,
        exit_reason: exitReason,
        exit_triggered_by: 'user_manual',
      })
      setShowClose(false)
      setExpanded(false)
      setDetail(null)
      onRefresh?.()
    } catch (e) {
      setCloseError(e.message)
    } finally {
      setClosing(false)
    }
  }

  const handleMarkHold = () => {
    setShowMarkInput(false)
    setShowClose(false)
  }

  const rev = summary?.latest_review ?? review
  const entry = summary?.entry ?? trade
  const cushionCallEntry =
    rev?.cushion_call_entry ??
    (entry.sell_call_strike != null && entry.entry_spot != null
      ? entry.sell_call_strike - entry.entry_spot
      : null)
  const cushionPutEntry =
    rev?.cushion_put_entry ??
    (entry.sell_put_strike != null && entry.entry_spot != null
      ? entry.entry_spot - entry.sell_put_strike
      : null)

  const strikes =
    trade.sell_put_strike && trade.sell_call_strike
      ? `${trade.sell_put_strike}PE / ${trade.sell_call_strike}CE`
      : legDescription(trade)

  const pnlPreview = previewPnl(trade, exitPremium)

  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="w-full text-left p-3 hover:bg-gray-900/80 transition-colors"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`text-xs px-2 py-0.5 rounded border ${
                trade.trade_type === 'LIVE'
                  ? 'bg-blue-900/40 text-blue-400 border-blue-700'
                  : 'bg-gray-800 text-gray-400 border-gray-600'
              }`}
            >
              {trade.trade_type}
            </span>
            <span className="text-sm font-bold text-white">
              {trade.strategy.replace(/_/g, ' ')}
            </span>
            <span className="text-xs text-gray-500 font-mono">{trade.trade_id}</span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`text-xs px-2 py-0.5 rounded border ${
                trade.status === 'OPEN'
                  ? 'bg-green-900/40 text-green-400 border-green-700'
                  : 'bg-gray-800 text-gray-500 border-gray-600'
              }`}
            >
              {trade.status}
            </span>
            {trade.dte_at_entry != null && (
              <span className="text-xs text-gray-500">{trade.dte_at_entry} DTE at entry</span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap justify-between items-center mt-2 gap-2">
          <span className="text-xs text-gray-400 font-mono">{strikes}</span>
          <div className="flex items-center gap-3">
            {displayPnl != null && (
              <span
                className={`text-sm font-mono font-bold ${
                  displayPnl >= 0 ? 'text-green-400' : 'text-red-400'
                }`}
              >
                {fmtMoney(displayPnl)}
                {displayPnlPct != null && (
                  <span className="text-gray-500 font-normal ml-1">({displayPnlPct}%)</span>
                )}
              </span>
            )}
            <span className={`text-xs px-2 py-0.5 rounded border ${badge.cls}`}>{badge.label}</span>
            <span className="text-xs text-blue-400">{expanded ? '▲' : '▼'} Details</span>
          </div>
        </div>
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t border-gray-700">
          {loading && <p className="text-xs text-gray-500 py-2">Loading trade detail…</p>}

          {(pendingReview || review?.review_status === 'PENDING_INPUT') && (
            <div className="bg-amber-900/20 border border-amber-700 rounded-lg p-3 space-y-2">
              <p className="text-amber-400 text-sm font-bold">⚠ Scheduled review needs premium</p>
              <p className="text-amber-300/80 text-xs">
                Submit Sensibull JSON or simple premium below to complete today&apos;s review.
              </p>
              <button
                type="button"
                onClick={() => {
                  setShowMarkInput(true)
                  setMarkMode('sensibull')
                  onFocusPending?.()
                }}
                className="text-xs text-blue-400 hover:text-blue-300"
              >
                Open mark form ↓
              </button>
            </div>
          )}

          {rev && rev.review_status === 'COMPLETED' && (
            <div className="space-y-1 border-t border-gray-700 pt-3">
              <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">
                Conditions — Entry → Today
              </p>
              <CompareRow label="IV Rank" entry={entry.iv_rank_at_entry} today={rev.iv_rank_today} />
              <CompareRow label="RSI" entry={entry.rsi_at_entry} today={rev.rsi_today} />
              <CompareRow
                label="BB Width"
                entry={entry.bb_width_at_entry}
                today={rev.bb_width_today}
              />
              <CompareRow
                label="Cushion CE"
                entry={cushionCallEntry}
                today={rev.cushion_call_today}
                unit=" pts"
              />
              <CompareRow
                label="Cushion PE"
                entry={cushionPutEntry}
                today={rev.cushion_put_today}
                unit=" pts"
              />
              <CompareRow
                label="Delta CE"
                entry={entry.sell_call_delta_at_entry ?? rev.short_call_delta_entry}
                today={rev.short_call_delta_today}
              />
              <CompareRow
                label="Net Theta"
                entry={entry.net_theta_at_entry ?? rev.net_theta_entry}
                today={rev.net_theta_today}
                prefix="₹"
                suffix="/day"
              />
              <CompareRow
                label="Call OI"
                entry={rev.call_oi_at_entry}
                today={rev.call_oi_today}
                showPct
                changePct={rev.call_oi_change_pct}
              />
            </div>
          )}

          {rev && rev.exit_signal && rev.exit_signal !== 'HOLD' && (
            <div
              className={`rounded-lg border p-3 space-y-2 ${
                rev.exit_signal === 'HARD_EXIT'
                  ? 'bg-red-900/20 border-red-700'
                  : 'bg-amber-900/20 border-amber-700'
              }`}
            >
              <div className="flex items-center justify-between">
                <span
                  className={`text-sm font-bold ${
                    rev.exit_signal === 'HARD_EXIT' ? 'text-red-400' : 'text-amber-400'
                  }`}
                >
                  {rev.exit_signal === 'HARD_EXIT' ? '✗ HARD EXIT' : '⚠ SOFT EXIT'}
                </span>
                <span className="text-xs text-gray-500">
                  {rev.check_slot} · {rev.review_time?.slice(0, 16)}
                </span>
              </div>
              <div className="flex flex-wrap gap-1">
                {(rev.exit_reason_codes || []).map((code) => (
                  <span
                    key={code}
                    className="text-xs px-2 py-0.5 rounded border border-amber-700 bg-amber-900/20 text-amber-300"
                  >
                    {code.replace(/_/g, ' ')}
                  </span>
                ))}
              </div>
              {rev.reasoning && (
                <p className="text-sm text-gray-300 leading-relaxed">{rev.reasoning}</p>
              )}
              {trade.status === 'OPEN' && (
                <div className="flex gap-3 pt-1">
                  <button
                    type="button"
                    onClick={() => {
                      setShowClose(true)
                      setShowMarkInput(false)
                    }}
                    className="flex-1 bg-red-700 hover:bg-red-600 text-white font-bold py-2 rounded-lg text-sm transition-colors"
                  >
                    Close Trade
                  </button>
                  {rev.exit_signal === 'SOFT_EXIT' && (
                    <button
                      type="button"
                      onClick={handleMarkHold}
                      className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 font-bold py-2 rounded-lg text-sm transition-colors"
                    >
                      Hold — Check EOD
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {marks.length > 0 && (
            <div className="border-t border-gray-700 pt-3">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs text-gray-500 uppercase tracking-wide">Mark History</p>
                {trade.status === 'OPEN' && (
                  <button
                    type="button"
                    onClick={() => {
                      setShowMarkInput((v) => !v)
                      setShowClose(false)
                    }}
                    className="text-xs text-blue-400 hover:text-blue-300"
                  >
                    + Mark Today
                  </button>
                )}
              </div>
              <div className="space-y-1">
                {[...marks].reverse().slice(0, 5).map((m) => (
                  <div key={m.mark_id} className="flex justify-between items-center text-xs gap-2">
                    <span className="text-gray-500 font-mono">{m.mark_date}</span>
                    <span className="text-gray-400">{m.spot?.toLocaleString('en-IN')}</span>
                    <span className={m.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                      {fmtMoney(m.unrealized_pnl)}
                    </span>
                    {m.exit_alert !== 'NONE' && (
                      <span className="text-amber-400">⚠ {m.exit_alert.replace(/_/g, ' ')}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {trade.status === 'OPEN' && marks.length === 0 && (
            <div className="border-t border-gray-700 pt-3">
              <button
                type="button"
                onClick={() => setShowMarkInput((v) => !v)}
                className="text-xs text-blue-400 hover:text-blue-300"
              >
                + Mark Today
              </button>
            </div>
          )}

          {showMarkInput && trade.status === 'OPEN' && (
            <div className="border-t border-gray-700 pt-3 space-y-2">
              <p className="text-xs text-gray-500 uppercase tracking-wide">Add Today&apos;s Mark</p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setMarkMode('simple')}
                  className={`text-xs px-3 py-1 rounded border transition-colors ${
                    markMode === 'simple'
                      ? 'border-blue-600 text-blue-400 bg-blue-900/20'
                      : 'border-gray-700 text-gray-500'
                  }`}
                >
                  Simple
                </button>
                <button
                  type="button"
                  onClick={() => setMarkMode('sensibull')}
                  className={`text-xs px-3 py-1 rounded border transition-colors ${
                    markMode === 'sensibull'
                      ? 'border-blue-600 text-blue-400 bg-blue-900/20'
                      : 'border-gray-700 text-gray-500'
                  }`}
                >
                  Sensibull JSON
                </button>
              </div>

              {markMode === 'simple' && (
                <div className="space-y-2">
                  <div>
                    <p className="text-xs text-gray-500 mb-1">
                      Current premium to close: {legDescription(trade)}
                    </p>
                    <input
                      type="number"
                      step="0.05"
                      placeholder="e.g. 18.50"
                      value={markPremium}
                      onChange={(e) => setMarkPremium(e.target.value)}
                      className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
                    />
                  </div>
                  <input
                    type="text"
                    placeholder="Note (optional)"
                    value={markNote}
                    onChange={(e) => setMarkNote(e.target.value)}
                    className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
                  />
                </div>
              )}

              {markMode === 'sensibull' && (
                <div className="space-y-2">
                  <p className="text-xs text-gray-500">Paste Sensibull position JSON:</p>
                  <textarea
                    rows={6}
                    placeholder={'{\n  "spot": 23969,\n  "legs": [...],\n  "summary": { "net_premium_to_close": 18.5 }\n}'}
                    value={snapshotJson}
                    onChange={(e) => setSnapshotJson(e.target.value)}
                    className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-xs font-mono text-white placeholder-gray-600 focus:outline-none focus:border-blue-500"
                  />
                </div>
              )}

              {(jsonError || markResult) && (
                <p className={`text-xs ${jsonError ? 'text-red-400' : 'text-gray-400'}`}>
                  {jsonError ||
                    (markResult?.exit_alert && markResult.exit_alert !== 'NONE'
                      ? `⚠ ${markResult.alert_detail || markResult.exit_alert}`
                      : `✓ Mark saved — P&L: ${fmtMoney(markResult?.unrealized_pnl)}`)}
                </p>
              )}

              <button
                type="button"
                onClick={handleSubmitMark}
                disabled={
                  submitting ||
                  (markMode === 'simple' ? !markPremium : !snapshotJson)
                }
                className="w-full bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 text-white font-bold py-2 rounded-lg text-sm transition-colors"
              >
                {submitting ? 'Saving...' : 'Save Mark'}
              </button>
            </div>
          )}

          {showClose && trade.status === 'OPEN' && (
            <div className="border-t border-red-900 pt-3 space-y-2">
              <p className="text-xs text-red-400 uppercase tracking-wide font-bold">Close Trade</p>
              <div>
                <p className="text-xs text-gray-500 mb-1">Exit premium (cost to close all legs):</p>
                <input
                  type="number"
                  step="0.05"
                  placeholder="e.g. 15.00"
                  value={exitPremium}
                  onChange={(e) => setExitPremium(e.target.value)}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-red-500"
                />
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">Exit reason:</p>
                <select
                  value={exitReason}
                  onChange={(e) => setExitReason(e.target.value)}
                  className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-red-500"
                >
                  <option value="50_pct_profit">50% profit target</option>
                  <option value="stop_loss">Stop loss</option>
                  <option value="day4_close">Day 4 mandatory close</option>
                  <option value="breach">Short strike breached</option>
                  <option value="manual">Manual decision</option>
                </select>
              </div>
              {pnlPreview != null && (
                <div className="bg-gray-900 rounded p-2 text-xs">
                  <span className="text-gray-500">P&L Preview: </span>
                  <span
                    className={`font-bold ${
                      pnlPreview >= 0 ? 'text-green-400' : 'text-red-400'
                    }`}
                  >
                    {fmtMoney(pnlPreview)} ({pnlPreview >= 0 ? 'WIN' : 'LOSS'})
                  </span>
                </div>
              )}
              {closeError && <p className="text-red-400 text-xs">{closeError}</p>}
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={handleConfirmClose}
                  disabled={!exitPremium || closing}
                  className="flex-1 bg-red-700 hover:bg-red-600 disabled:bg-gray-700 text-white font-bold py-2 rounded-lg text-sm transition-colors"
                >
                  {closing ? 'Closing...' : 'Confirm Close'}
                </button>
                <button
                  type="button"
                  onClick={() => setShowClose(false)}
                  className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 font-bold py-2 rounded-lg text-sm transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
