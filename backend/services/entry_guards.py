"""Entry pipeline guardrails (pre-flight and post-LLM). No LangChain dependency."""

from typing import List, Optional

from models.market import MarketData
from models.options import OIAnalysis
from models.strategy import StrategyRecommendation
from services.analysis_digest import data_integrity_status
from services.expiry_utils import (
    days_to_expiry,
    is_below_min_entry_dte,
    is_expiry_day,
    is_past_0dte_entry_cutoff,
    ist_now,
    session_phase_with_expiry,
)


def override_to_wait(result: StrategyRecommendation, reason: str, had_model_trade: bool) -> None:
    prev = result.strategy
    if had_model_trade and prev != "WAIT":
        extras = f" Model had suggested {prev}."
        result.reasoning = (
            f"Guardrail WAIT: {reason}.{extras}\n---\nPrior analyst reasoning:\n{(result.reasoning or '')}"
        )
    else:
        if not result.wait_reason:
            result.wait_reason = f"[Guardrail] {reason}"
        result.integrity_note = (result.integrity_note + " | " if result.integrity_note else "") + reason
    result.strategy = "WAIT"
    result.confidence = "LOW"
    result.integrity_blocked = True
    result.sell_put_strike = result.buy_put_strike = None
    result.sell_call_strike = result.buy_call_strike = None
    result.sell_put_premium = result.buy_put_premium = None
    result.sell_call_premium = result.buy_call_premium = None
    result.net_premium = result.max_profit = result.max_loss = None
    result.lower_breakeven = result.upper_breakeven = None
    result.conservative_net_premium = None
    result.conservative_max_profit = None
    result.conservative_max_loss = None
    result.conservative_lower_breakeven = None
    result.conservative_upper_breakeven = None
    if had_model_trade and prev != "WAIT":
        result.wait_reason = f"[Guardrail] {reason} (prior: {prev})."
        result.conflicting_signals = list(result.conflicting_signals or []) + [f"System guardrail: {reason}"]


def stamp_integrity_meta(
    result: StrategyRecommendation,
    market: MarketData,
    chain,
    oi: Optional[OIAnalysis] = None,
) -> None:
    mq, cq, issues = data_integrity_status(market, chain)
    expiry = str(getattr(chain, "attrs", {}).get("expiry") or "") if chain is not None else ""
    ist_label, phase = session_phase_with_expiry(expiry)
    result.snapshot_time_ist = ist_label
    result.session_phase = phase
    result.quotes_live = mq
    result.chain_live = cq
    result.data_trade_ready = mq and cq
    result.option_expiry = expiry or None
    result.days_to_expiry = days_to_expiry(expiry)
    result.is_expiry_day = is_expiry_day(expiry)
    result.integrity_note = "; ".join(issues) if issues else None
    result.spot_snapshot = round(float(market.nifty_spot), 2)
    if oi is not None:
        result.pcr_snapshot = round(float(oi.pcr), 2)
        result.iv_rank_snapshot = round(float(oi.iv_rank), 1)


def session_entry_blockers(now=None) -> List[str]:
    """Hard blockers for new entries outside the IST session window."""
    from config import (
        SESSION_CLOSE_HOUR,
        SESSION_CLOSE_MINUTE,
        SESSION_OPEN_HOUR,
        SESSION_OPEN_MINUTE,
    )

    ref = now or ist_now()
    if ref.weekday() >= 5:
        return ["Market closed (weekend)."]

    mins = ref.hour * 60 + ref.minute
    open_m = SESSION_OPEN_HOUR * 60 + SESSION_OPEN_MINUTE
    close_m = SESSION_CLOSE_HOUR * 60 + SESSION_CLOSE_MINUTE
    open_label = f"{SESSION_OPEN_HOUR:02d}:{SESSION_OPEN_MINUTE:02d}"
    close_label = f"{SESSION_CLOSE_HOUR:02d}:{SESSION_CLOSE_MINUTE:02d}"

    if mins < open_m:
        return [f"Before entry window ({open_label} IST). Current: {ref.strftime('%H:%M')} IST."]
    if mins >= close_m:
        return [f"After entry window ({close_label} IST). Current: {ref.strftime('%H:%M')} IST."]
    return []


def collect_hard_blockers(market: MarketData, oi: OIAnalysis, chain) -> List[str]:
    """Shared hard blockers for pre-flight (and parity with post-LLM guards)."""
    from config import MIN_ENTRY_DTE, MIN_IV_RANK, MIN_VIX

    reasons: List[str] = []
    mq, cq, _ = data_integrity_status(market, chain)
    if not (mq and cq):
        reasons.append("Live quote and/or live option chain not available.")
    if float(market.vix) < MIN_VIX:
        reasons.append(f"VIX {float(market.vix):.1f} below minimum {MIN_VIX}.")
    if float(oi.iv_rank) < MIN_IV_RANK:
        reasons.append(f"IV Rank {float(oi.iv_rank):.1f}% below minimum {MIN_IV_RANK}%.")
    expiry = str(getattr(chain, "attrs", {}).get("expiry") or "") if chain is not None else ""
    if is_below_min_entry_dte(expiry):
        dte = days_to_expiry(expiry)
        reasons.append(f"Chain DTE {dte} below minimum {MIN_ENTRY_DTE}.")
    if is_past_0dte_entry_cutoff(expiry):
        reasons.append("Past 0 DTE entry cutoff.")
    reasons.extend(session_entry_blockers())
    return reasons


def preflight_wait(
    market: MarketData,
    oi: OIAnalysis,
    chain,
) -> Optional[StrategyRecommendation]:
    """Return WAIT before LLM when hard blockers fire; None means proceed."""
    reasons = collect_hard_blockers(market, oi, chain)
    if not reasons:
        return None
    combined = "; ".join(reasons)
    result = StrategyRecommendation(
        strategy="WAIT",
        confidence="LOW",
        reasoning=f"Pre-flight check failed: {combined}",
        wait_reason=f"[Pre-flight] {combined}",
        integrity_blocked=True,
        preflight_blocked=True,
    )
    stamp_integrity_meta(result, market, chain, oi)
    return result


def apply_vix_guard(result: StrategyRecommendation, market: MarketData) -> None:
    from config import MIN_VIX

    if result.strategy == "WAIT":
        return
    vix = float(market.vix)
    if vix < MIN_VIX:
        override_to_wait(
            result,
            f"VIX {vix:.2f} is below minimum {MIN_VIX} — premiums too thin for weekly IC.",
            True,
        )


def apply_iv_rank_guard(result: StrategyRecommendation, oi: OIAnalysis) -> None:
    from config import MIN_IV_RANK

    if result.strategy == "WAIT":
        return
    iv_rank = float(oi.iv_rank)
    if iv_rank < MIN_IV_RANK:
        override_to_wait(
            result,
            f"IV Rank {iv_rank:.1f}% is below minimum {MIN_IV_RANK}% for premium selling.",
            True,
        )


def _row_for_strike(chain, strike):
    if chain is None or strike is None or getattr(chain, "empty", True):
        return None
    row = chain[chain["strike"] == strike]
    if row.empty:
        row = chain.iloc[(chain["strike"] - strike).abs().argsort()[:1]]
    return row.iloc[0]


def _downgrade_confidence(result: StrategyRecommendation) -> None:
    if result.confidence == "HIGH":
        result.confidence = "MEDIUM"


def _leg_delta(chain, strike, side: str) -> Optional[float]:
    if strike is None or chain is None or getattr(chain, "empty", True):
        return None
    from services.strike_candidates import _delta

    return _delta(chain, int(strike), side)


def _leg_theta(chain, strike, side: str) -> Optional[float]:
    row = _row_for_strike(chain, strike)
    if row is None:
        return None
    col = f"{side}_theta"
    try:
        val = float(row.get(col, 0) or 0)
    except (TypeError, ValueError):
        return None
    return val


def apply_theta_guard(
    result: StrategyRecommendation,
    chain,
    lot_multiplier: float,
) -> None:
    """Validate daily theta for iron structures. Soft warn; hard WAIT if critically low."""
    from config import MIN_THETA_PER_DAY

    if result.strategy not in ("IRON_CONDOR", "IRON_BUTTERFLY"):
        return
    if chain is None or getattr(chain, "empty", True):
        return

    # Chain theta is long-oriented (negative decay). Short legs: -1 * theta; long legs: +1 * theta.
    net_per_contract = 0.0
    legs = [
        (result.sell_put_strike, "put", -1),
        (result.buy_put_strike, "put", 1),
        (result.sell_call_strike, "call", -1),
        (result.buy_call_strike, "call", 1),
    ]
    for strike, side, position_sign in legs:
        if strike is None:
            continue
        theta = _leg_theta(chain, strike, side)
        if theta is None:
            continue
        net_per_contract += position_sign * theta

    # Short premium earns positive theta when net_per_contract is negative (long θ < 0).
    theta_per_day = round(abs(net_per_contract) * float(lot_multiplier), 2)
    result.theta_per_day = theta_per_day

    if theta_per_day < MIN_THETA_PER_DAY:
        msg = (
            f"Daily theta ₹{theta_per_day:.0f} is below minimum ₹{MIN_THETA_PER_DAY:.0f} "
            "— insufficient time decay."
        )
        result.conflicting_signals = list(result.conflicting_signals or []) + [msg]
        _downgrade_confidence(result)
        if theta_per_day < MIN_THETA_PER_DAY * 0.5:
            override_to_wait(
                result,
                f"Theta/day ₹{theta_per_day:.0f} is critically below minimum ₹{MIN_THETA_PER_DAY:.0f}.",
                True,
            )


def apply_margin_guard(
    result: StrategyRecommendation,
    funds: Optional[dict],
) -> None:
    """Block when live available margin cannot support NUM_LOTS (no auto downscale)."""
    from config import MARGIN_BUFFER_PCT, MARGIN_ESTIMATE_MULT

    if result.strategy == "WAIT":
        return
    if not funds:
        result.margin_note = "Margin check skipped — no funds payload."
        return

    is_mock = bool(funds.get("is_mock", True))
    if is_mock:
        result.margin_note = "Margin check skipped — funds data is mock."
        return

    try:
        available = float(funds.get("available_margin", 0) or 0)
    except (TypeError, ValueError):
        result.margin_note = "Margin check skipped — invalid available_margin."
        return

    base_loss = abs(
        result.conservative_max_loss
        if result.conservative_max_loss is not None
        else (result.max_loss or 0)
    )
    estimated_margin = round(base_loss * MARGIN_ESTIMATE_MULT, 2)
    required_with_buffer = round(estimated_margin * (1 + MARGIN_BUFFER_PCT / 100), 2)

    result.available_margin = round(available, 2)
    result.estimated_margin = estimated_margin

    if available < required_with_buffer:
        override_to_wait(
            result,
            f"Insufficient margin: available ₹{available:,.0f} vs estimated required "
            f"₹{required_with_buffer:,.0f} (base loss ₹{base_loss:,.0f} × {MARGIN_ESTIMATE_MULT} + "
            f"{MARGIN_BUFFER_PCT:.0f}% buffer).",
            True,
        )


def apply_net_delta_guard(result: StrategyRecommendation, chain) -> None:
    """Soft warn when iron structure has excessive directional delta bias."""
    from config import MAX_NET_DELTA

    if result.strategy not in ("IRON_CONDOR", "IRON_BUTTERFLY"):
        return
    if chain is None or getattr(chain, "empty", True):
        return

    legs = [
        (result.sell_put_strike, "put", -1),
        (result.buy_put_strike, "put", 1),
        (result.sell_call_strike, "call", -1),
        (result.buy_call_strike, "call", 1),
    ]
    net = 0.0
    found = 0
    for strike, side, qty in legs:
        if strike is None:
            continue
        d = _leg_delta(chain, strike, side)
        if d is None:
            continue
        net += qty * d
        found += 1
    if found < 2:
        return

    result.net_delta = round(net, 4)
    if abs(net) > MAX_NET_DELTA:
        result.conflicting_signals = list(result.conflicting_signals or []) + [
            f"Net delta {net:.4f} exceeds ±{MAX_NET_DELTA} — structure has directional bias."
        ]
        _downgrade_confidence(result)


def apply_sr_buffer_guard(result: StrategyRecommendation, oi: OIAnalysis) -> None:
    """Soft warn when shorts are on/at OI walls (reframed: must sit inside S/R range)."""
    from config import MIN_SR_BUFFER_PTS

    if result.strategy == "WAIT":
        return

    support = int(oi.support)
    resistance = int(oi.resistance)
    warnings: List[str] = []

    if result.sell_put_strike is not None:
        floor = support + MIN_SR_BUFFER_PTS
        if result.sell_put_strike <= floor:
            warnings.append(
                f"Short put {result.sell_put_strike} is not above support {support} "
                f"+ {MIN_SR_BUFFER_PTS} pts buffer (min {floor + 1}) — risk if support breaks."
            )

    if result.sell_call_strike is not None:
        ceiling = resistance - MIN_SR_BUFFER_PTS
        if result.sell_call_strike >= ceiling:
            warnings.append(
                f"Short call {result.sell_call_strike} is not below resistance {resistance} "
                f"- {MIN_SR_BUFFER_PTS} pts buffer (max {ceiling - 1}) — risk if resistance breaks."
            )

    if warnings:
        result.conflicting_signals = list(result.conflicting_signals or []) + warnings
        _downgrade_confidence(result)


def apply_spread_width_guard(result: StrategyRecommendation, chain) -> None:
    """Soft warn when bid-ask spreads are wide (poor liquidity / slippage)."""
    from config import MAX_BID_ASK_SPREAD

    if result.strategy == "WAIT" or chain is None or getattr(chain, "empty", True):
        return

    wide_legs: List[str] = []
    for strike, side, leg_name in [
        (result.sell_put_strike, "put", "sell_put"),
        (result.buy_put_strike, "put", "buy_put"),
        (result.sell_call_strike, "call", "sell_call"),
        (result.buy_call_strike, "call", "buy_call"),
    ]:
        if strike is None:
            continue
        row = _row_for_strike(chain, strike)
        if row is None:
            continue
        try:
            bid = float(row.get(f"{side}_bid", 0) or 0)
            ask = float(row.get(f"{side}_ask", 0) or 0)
        except (TypeError, ValueError):
            continue
        if bid > 0 and ask > 0:
            spread = ask - bid
            if spread > MAX_BID_ASK_SPREAD:
                wide_legs.append(
                    f"{leg_name} ({strike} {side.upper()}): spread ₹{spread:.1f}"
                )

    if wide_legs:
        result.conflicting_signals = list(result.conflicting_signals or []) + [
            f"Wide bid-ask spreads (>{MAX_BID_ASK_SPREAD:.0f}): " + "; ".join(wide_legs)
        ]
        _downgrade_confidence(result)


def apply_post_financial_guards(
    result: StrategyRecommendation,
    chain,
    funds: Optional[dict] = None,
    lot_multiplier: Optional[float] = None,
    oi: Optional[OIAnalysis] = None,
) -> None:
    """Post-LLM guards after financials: structure quality, theta, margin."""
    if result.strategy == "WAIT":
        return
    if lot_multiplier is None:
        from config import NUM_LOTS
        from services.chain_utils import lot_size_from_chain

        lot_multiplier = float(lot_size_from_chain(chain) * NUM_LOTS)

    apply_net_delta_guard(result, chain)
    apply_theta_guard(result, chain, lot_multiplier)
    if result.strategy != "WAIT":
        apply_margin_guard(result, funds)
    if result.strategy != "WAIT" and oi is not None:
        apply_sr_buffer_guard(result, oi)
    if result.strategy != "WAIT":
        apply_spread_width_guard(result, chain)


def should_skip_evaluator(strategy: StrategyRecommendation) -> bool:
    """Skip evaluator LLM when pre-flight already returned WAIT."""
    return bool(getattr(strategy, "preflight_blocked", False))


def evaluator_skipped_sse_payload(reason: str = "pre_flight") -> dict:
    """SSE event shape when evaluator is skipped (e.g. pre-flight WAIT)."""
    return {"agent": "evaluator", "status": "skipped", "reason": reason}
