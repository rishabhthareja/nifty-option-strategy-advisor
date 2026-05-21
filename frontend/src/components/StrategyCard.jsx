const STRATEGY_COLORS = {
  IRON_CONDOR: 'text-blue-400 border-blue-600 bg-blue-900/20',
  IRON_BUTTERFLY: 'text-cyan-400 border-cyan-600 bg-cyan-900/20',
  BULL_PUT_SPREAD: 'text-green-400 border-green-600 bg-green-900/20',
  BEAR_CALL_SPREAD: 'text-red-400 border-red-600 bg-red-900/20',
  SHORT_STRANGLE: 'text-purple-400 border-purple-600 bg-purple-900/20',
  WAIT: 'text-yellow-400 border-yellow-600 bg-yellow-900/20',
}

const CONFIDENCE_COLORS = {
  HIGH: 'bg-green-900/40 text-green-400 border-green-700',
  MEDIUM: 'bg-yellow-900/40 text-yellow-400 border-yellow-700',
  LOW: 'bg-red-900/40 text-red-400 border-red-700',
}

import { expirySummary, legSymbol } from '../utils/formatExpiry'

const PHASE_LABELS = {
  REGULAR_SESSION: 'Regular',
  OPENING_HOUR: 'Opening',
  CLOSING_HOUR: 'Closing',
  EXPIRY_DAY: 'Expiry day (0 DTE)',
  SESSION_CLOSED: 'Closed',
}

function fmt(v) {
  return v != null ? `₹${Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2 })}` : '—'
}

function LiveBadge({ ok, label }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${ok ? 'bg-green-900/30 text-green-400 border-green-800' : 'bg-red-900/30 text-red-400 border-red-800'}`}>
      {label}: {ok ? 'LIVE' : 'MOCK'}
    </span>
  )
}

function LegRow({ action, strike, type, premium, expiry }) {
  if (!strike) return null
  const sym = legSymbol(expiry, strike, type)
  return (
    <div className="flex items-center justify-between bg-gray-900 rounded px-3 py-2">
      <div className="flex flex-col gap-0.5 min-w-0">
        <div className="flex items-center gap-2">
          <span className={`text-xs font-bold px-2 py-0.5 rounded shrink-0 ${action === 'SELL' ? 'bg-red-900/50 text-red-400' : 'bg-green-900/50 text-green-400'}`}>
            {action}
          </span>
          <span className="text-sm font-mono text-white">{strike} {type}</span>
        </div>
        {sym && <span className="text-[10px] font-mono text-gray-500 truncate pl-1">{sym}</span>}
      </div>
      <span className="text-sm font-mono text-yellow-400 shrink-0 ml-2">{premium != null ? `${premium.toFixed(2)}` : '—'}</span>
    </div>
  )
}

function ExpiryBar({ data }) {
  const exp = expirySummary(data)
  if (!exp) return null
  return (
    <div className={`rounded-lg border px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs ${data.is_expiry_day ? 'bg-amber-900/20 border-amber-700' : 'bg-gray-900/80 border-gray-700'}`}>
      <span className="text-gray-500 uppercase tracking-wide">Expiry</span>
      <span className="text-white font-semibold">{exp.primary}</span>
      <span className="font-mono text-cyan-400/90">{exp.badge}</span>
      {exp.dtePart && (
        <span className={`font-bold ${data.is_expiry_day ? 'text-amber-400' : 'text-gray-400'}`}>{exp.dtePart}</span>
      )}
    </div>
  )
}

function DataIntegrityBar({ data }) {
  const tradeReady = data.data_trade_ready === true
  const blocked = data.integrity_blocked === true

  return (
    <div className={`rounded-lg border p-3 space-y-2 text-xs ${blocked ? 'bg-red-900/20 border-red-800' : tradeReady ? 'bg-green-900/10 border-green-900' : 'bg-amber-900/20 border-amber-800'}`}>
      <div className="flex flex-wrap items-center gap-2 justify-between">
        <span className="text-gray-400 font-mono">{data.snapshot_time_ist || '—'}</span>
        {data.session_phase && (
          <span className="text-gray-500">{PHASE_LABELS[data.session_phase] || data.session_phase}</span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <LiveBadge ok={data.quotes_live === true} label="Quotes" />
        <LiveBadge ok={data.chain_live === true} label="Chain" />
        <span className={`text-xs px-2 py-0.5 rounded border font-bold ${tradeReady ? 'bg-green-900/40 text-green-400 border-green-700' : 'bg-amber-900/40 text-amber-300 border-amber-700'}`}>
          TRADE READY: {tradeReady ? 'YES' : 'NO'}
        </span>
      </div>
      {blocked && (
        <p className="text-red-300 text-sm">Guardrail applied — recommendation overridden to WAIT.</p>
      )}
      {data.integrity_note && (
        <p className="text-gray-400 leading-snug">{data.integrity_note}</p>
      )}
    </div>
  )
}

function PnlGrid({ title, titleClass, netPremium, maxProfit, maxLoss, lowerBe, upperBe, strategy }) {
  const maxLossLabel = maxLoss != null ? fmt(maxLoss) : (strategy === 'SHORT_STRANGLE' ? 'Unlimited' : '—')
  return (
    <div className="space-y-2">
      <p className={`text-xs uppercase tracking-wide ${titleClass}`}>{title}</p>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="bg-gray-900 rounded p-2">
          <div className="text-gray-500">Net Premium</div>
          <div className="text-green-400 font-bold text-sm">{netPremium?.toFixed(2) ?? '—'}</div>
        </div>
        <div className="bg-gray-900 rounded p-2">
          <div className="text-gray-500">Max Profit</div>
          <div className="text-green-400 font-bold text-sm">{fmt(maxProfit)}</div>
        </div>
        <div className="bg-gray-900 rounded p-2">
          <div className="text-gray-500">Max Loss</div>
          <div className="text-red-400 font-bold text-sm">{maxLossLabel}</div>
        </div>
        <div className="bg-gray-900 rounded p-2">
          <div className="text-gray-500">Breakevens</div>
          <div className="text-yellow-400 font-bold text-sm">
            {lowerBe?.toFixed(0) ?? '—'} — {upperBe?.toFixed(0) ?? '—'}
          </div>
        </div>
      </div>
    </div>
  )
}

export default function StrategyCard({ data }) {
  if (!data) return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4">
      <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase mb-3">Strategy Recommendation</h2>
      <p className="text-gray-600 text-sm">Waiting for strategy analysis...</p>
    </div>
  )

  const stColor = STRATEGY_COLORS[data.strategy] || STRATEGY_COLORS.WAIT
  const confColor = CONFIDENCE_COLORS[data.confidence] || CONFIDENCE_COLORS.LOW
  const hasConservative = data.conservative_net_premium != null
  const expiry = data.option_expiry

  return (
    <div className="bg-[#1a1a1a] border border-gray-800 rounded-lg p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-blue-400 tracking-widest uppercase">Strategy Recommendation</h2>
        <span className={`text-xs border px-2 py-0.5 rounded font-bold ${confColor}`}>{data.confidence}</span>
      </div>

      <DataIntegrityBar data={data} />

      <ExpiryBar data={data} />

      <div className={`border rounded-lg px-4 py-3 text-center ${stColor}`}>
        <span className="text-2xl font-bold tracking-widest">{data.strategy.replace(/_/g, ' ')}</span>
      </div>

      {data.structure_note && (
        <p className="text-xs text-cyan-300/90 bg-cyan-950/30 border border-cyan-900 rounded p-2 leading-snug">
          {data.structure_note}
        </p>
      )}

      {data.strategy !== 'WAIT' ? (
        <>
          <div className="space-y-2">
            <p className="text-xs text-gray-500 uppercase tracking-wide">Order Legs (LTP approx)</p>
            <LegRow action="SELL" strike={data.sell_put_strike} type="PE" premium={data.sell_put_premium} expiry={expiry} />
            <LegRow action="BUY" strike={data.buy_put_strike} type="PE" premium={data.buy_put_premium} expiry={expiry} />
            <LegRow action="SELL" strike={data.sell_call_strike} type="CE" premium={data.sell_call_premium} expiry={expiry} />
            <LegRow action="BUY" strike={data.buy_call_strike} type="CE" premium={data.buy_call_premium} expiry={expiry} />
          </div>

          <PnlGrid
            title="Mid / LTP estimate"
            titleClass="text-gray-500"
            netPremium={data.net_premium}
            maxProfit={data.max_profit}
            maxLoss={data.max_loss}
            lowerBe={data.lower_breakeven}
            upperBe={data.upper_breakeven}
            strategy={data.strategy}
          />

          {hasConservative && (
            <PnlGrid
              title="Conservative (short @ bid, long @ ask)"
              titleClass="text-amber-500"
              netPremium={data.conservative_net_premium}
              maxProfit={data.conservative_max_profit}
              maxLoss={data.conservative_max_loss}
              lowerBe={data.conservative_lower_breakeven}
              upperBe={data.conservative_upper_breakeven}
              strategy={data.strategy}
            />
          )}
        </>
      ) : (
        <div className="bg-yellow-900/20 border border-yellow-800 rounded p-3">
          <p className="text-yellow-300 text-sm">{data.wait_reason || 'Conditions not favourable for trading.'}</p>
        </div>
      )}

      {data.aligned_signals?.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Aligned Signals</p>
          <ul className="space-y-1">
            {data.aligned_signals.map((s, i) => (
              <li key={i} className="flex items-start gap-1.5 text-sm text-green-400">
                <span className="mt-0.5">✓</span><span>{s}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.conflicting_signals?.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Conflicting Signals</p>
          <ul className="space-y-1">
            {data.conflicting_signals.map((s, i) => (
              <li key={i} className="flex items-start gap-1.5 text-sm text-yellow-400">
                <span className="mt-0.5">⚠</span><span>{s}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <p className="text-xs text-gray-500 uppercase tracking-wide mb-1">Reasoning</p>
        <p className="text-sm text-gray-300 leading-relaxed">{data.reasoning}</p>
      </div>
    </div>
  )
}
