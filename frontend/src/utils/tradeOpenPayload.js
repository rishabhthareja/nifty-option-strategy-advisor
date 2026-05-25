/** Build POST /trade/open body from analysis outputs. */

const DEFAULT_LOT_SIZE = 65

function oiAtStrike(oi, strike, side) {
  if (!oi || strike == null) return null
  const list = side === 'put' ? oi.top_put_strikes : oi.top_call_strikes
  const hit = list?.find((r) => r.strike === strike)
  if (hit) return hit.oi
  if (side === 'put' && oi.support === strike) return oi.support_put_oi
  if (side === 'call' && oi.resistance === strike) return oi.resistance_call_oi
  return null
}

function deltaFromGreeks(greeks, strategy, side) {
  if (!greeks || !strategy) return null
  const strike = side === 'put' ? strategy.sell_put_strike : strategy.sell_call_strike
  if (strike == null) return null

  const candidates = greeks.strike_candidates || []
  for (const c of candidates) {
    if (side === 'put' && c.sell_put_strike === strike && c.short_put_delta != null) {
      return c.short_put_delta
    }
    if (side === 'call' && c.sell_call_strike === strike && c.short_call_delta != null) {
      return c.short_call_delta
    }
  }

  if (side === 'put' && greeks.sell_put?.strike === strike) return greeks.sell_put.delta
  if (side === 'call' && greeks.sell_call?.strike === strike) return greeks.sell_call.delta
  return null
}

function netThetaFromGreeks(greeks, strategy) {
  if (strategy?.theta_per_day != null) return strategy.theta_per_day
  if (!greeks?.sell_put || !greeks?.sell_call) return null
  const sp = greeks.sell_put.theta ?? 0
  const sc = greeks.sell_call.theta ?? 0
  const bp = greeks.buy_put?.theta ?? 0
  const bc = greeks.buy_call?.theta ?? 0
  return Math.round((-sp - sc + bp + bc) * 100) / 100
}

function netVegaFromGreeks(greeks) {
  if (!greeks?.sell_put || !greeks?.sell_call) return null
  const sp = greeks.sell_put.vega ?? 0
  const sc = greeks.sell_call.vega ?? 0
  const bp = greeks.buy_put?.vega ?? 0
  const bc = greeks.buy_call?.vega ?? 0
  return Math.round((-sp - sc + bp + bc) * 100) / 100
}

function rangePositionAtEntry(strategy, oi, market) {
  if (strategy?.range_position) return strategy.range_position
  const spot = strategy?.spot_snapshot ?? market?.nifty_spot
  if (!oi || spot == null || !oi.support || !oi.resistance) return null
  const width = oi.resistance - oi.support
  if (width <= 0) return null
  const util = ((spot - oi.support) / width) * 100
  if (util >= 85) return 'NEAR_RESISTANCE'
  if (util <= 15) return 'NEAR_SUPPORT'
  return 'MID_RANGE'
}

export function buildPaperTradeOpenPayload({ strategy, runId, oi, greeks, market, technical }) {
  if (!strategy || strategy.strategy === 'WAIT') {
    throw new Error('Cannot open trade for WAIT strategy')
  }

  const lotSize = strategy.lot_size ?? greeks?.lot_size ?? DEFAULT_LOT_SIZE

  return {
    trade_type: 'PAPER',
    run_id: runId,
    entry_spot: strategy.spot_snapshot ?? market?.nifty_spot,
    expiry_date: strategy.option_expiry,
    dte_at_entry: strategy.days_to_expiry ?? 0,
    strategy: strategy.strategy,
    sell_put_strike: strategy.sell_put_strike,
    buy_put_strike: strategy.buy_put_strike,
    sell_call_strike: strategy.sell_call_strike,
    buy_call_strike: strategy.buy_call_strike,
    entry_premium: strategy.conservative_net_premium ?? strategy.net_premium,
    max_profit: strategy.conservative_max_profit ?? strategy.max_profit,
    max_loss: strategy.conservative_max_loss ?? strategy.max_loss,
    lower_breakeven: strategy.conservative_lower_breakeven ?? strategy.lower_breakeven,
    upper_breakeven: strategy.conservative_upper_breakeven ?? strategy.upper_breakeven,
    lot_size: lotSize,
    num_lots: 1,
    iv_rank_at_entry: strategy.iv_rank_snapshot,
    vix_at_entry: market?.vix,
    pop_at_entry: strategy.est_pop_pct,
    reward_risk_at_entry: strategy.reward_risk,
    theta_per_day_at_entry: strategy.theta_per_day ?? netThetaFromGreeks(greeks, strategy),
    rsi_at_entry: technical?.rsi,
    bb_width_at_entry: technical?.bb_width,
    range_position_at_entry: rangePositionAtEntry(strategy, oi, market),
    spot_to_resistance_at_entry:
      strategy.spot_to_resistance_pts ??
      (oi && strategy.spot_snapshot != null
        ? Math.round((oi.resistance - strategy.spot_snapshot) * 10) / 10
        : null),
    spot_to_support_at_entry:
      strategy.spot_to_support_pts ??
      (oi && strategy.spot_snapshot != null
        ? Math.round((strategy.spot_snapshot - oi.support) * 10) / 10
        : null),
    pcr_at_entry: strategy.pcr_snapshot ?? oi?.pcr,
    sell_call_oi_at_entry: oiAtStrike(oi, strategy.sell_call_strike, 'call'),
    sell_put_oi_at_entry: oiAtStrike(oi, strategy.sell_put_strike, 'put'),
    max_pain_at_entry: oi?.max_pain,
    sell_call_delta_at_entry: deltaFromGreeks(greeks, strategy, 'call'),
    sell_put_delta_at_entry: deltaFromGreeks(greeks, strategy, 'put'),
    net_theta_at_entry: netThetaFromGreeks(greeks, strategy),
    net_vega_at_entry: netVegaFromGreeks(greeks),
  }
}

export function legDescription(trade) {
  const parts = []
  if (trade.sell_put_strike) parts.push(`Sell ${trade.sell_put_strike}PE`)
  if (trade.buy_put_strike) parts.push(`Buy ${trade.buy_put_strike}PE`)
  if (trade.sell_call_strike) parts.push(`Sell ${trade.sell_call_strike}CE`)
  if (trade.buy_call_strike) parts.push(`Buy ${trade.buy_call_strike}CE`)
  return parts.join(' + ') || trade.strategy
}

export function previewPnl(trade, exitPremium) {
  const ep = parseFloat(exitPremium)
  if (Number.isNaN(ep) || trade.entry_premium == null) return null
  return Math.round((trade.entry_premium - ep) * trade.lot_size * (trade.num_lots || 1) * 100) / 100
}
