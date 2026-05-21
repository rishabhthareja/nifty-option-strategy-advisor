from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from models.market import MarketData, TechnicalData
from models.options import OIAnalysis, GreeksData
from models.strategy import StrategyRecommendation
from config import GEMINI_MODEL, GOOGLE_API_KEY, NUM_LOTS
from services.analysis_digest import (
    data_integrity_status,
    format_atm_strip,
    format_suggested_legs_with_chain,
    iv_skew_line,
    positioning_bullets,
    strikes_valid_for_strategy,
)
from services.expiry_utils import (
    days_to_expiry,
    expiry_context_block,
    is_expiry_day,
    is_past_0dte_entry_cutoff,
    session_phase_with_expiry,
)
from services.market_metrics import (
    dual_expiry_summary,
    entry_timing_block,
    format_top_oi_strikes,
    intraday_context,
    reliability_legend,
)
from services.openalgo_client import OpenAlgoService

STRATEGY_FRAMEWORK = """
STRATEGY SELECTION FRAMEWORK:

1. IRON CONDOR — Sell OTM put + Buy further OTM put + Sell OTM call + Buy further OTM call
   REQUIRED: sell_put_strike MUST differ from sell_call_strike (e.g. short 23700 PE + short 23800 CE).
   Use when: Sideways market, IV_Rank > 40, PCR neutral 0.9–1.1, clear S/R range between shorts, VIX stable
   Ideal: RSI 45–55, MACD neutral, range-bound; wider breakevens than a fly
   Risk: Directional breakout beyond wings

2. IRON BUTTERFLY — Sell ATM (or near-ATM) put AND call at the SAME strike + buy wing put below + buy wing call above
   REQUIRED: sell_put_strike MUST equal sell_call_strike (e.g. both short 23750).
   Use when: Strong range/pin view, very high IV, spot near max pain; accept tighter breakevens for more credit
   Risk: Large gamma on 0 DTE / expiry day; pin breaks cause fast loss. On EXPIRY DAY prefer IRON_CONDOR or WAIT unless conviction is high.

3. BULL PUT SPREAD — Sell OTM put + Buy further OTM put
   Use when: Mild bullish trend, RSI > 55 not overbought, PCR > 1.1 (bullish), support holding
   Ideal: Price above SMA20 & SMA50, MACD bullish histogram, VIX declining
   Risk: Sharp reversal breaking support

4. BEAR CALL SPREAD — Sell OTM call + Buy further OTM call
   Use when: Mild bearish trend, RSI < 45 not oversold, PCR < 0.9 (bearish), resistance holding
   Ideal: Price below SMA20 & SMA50, MACD bearish, VIX rising
   Risk: Sharp breakout above resistance

5. SHORT STRANGLE — Sell OTM put + Sell OTM call (no hedge)
   Use when: Very high IV_Rank > 60, VIX > 18, extremely range-bound, high conviction
   High risk — maximum loss unlimited. Only recommend if all signals strongly aligned.

6. WAIT — Do not trade
   Use when: VIX < 12 (too low IV, poor premiums), IV_Rank < 30, conflicting signals,
   major event risk, RSI extreme (>75 or <25), trend unclear

DECISION LADDER (prefer selling structures):
- First decide regime: RANGE / MILD_BULL / MILD_BEAR / HIGH_RISK.
- RANGE + high IV: prefer IRON_CONDOR; if strong pin-to-strike conviction, use IRON_BUTTERFLY.
- MILD_BULL + support holding: prefer BULL_PUT_SPREAD over condor.
- MILD_BEAR + resistance holding: prefer BEAR_CALL_SPREAD over condor.
- Only use SHORT_STRANGLE when IV is very high and signals are strongly aligned; otherwise use defined-risk structures.
- If directional evidence is strong but opposite OI wall is too close, prefer WAIT over forcing a trade.

STRIKE SELECTION GUIDE:
- Iron condor: short put below spot near support; short call above spot near resistance (different strikes).
- Iron butterfly: short body at ATM or max pain; wings typically ±1–2 intervals from body.
- Never label IRON_CONDOR if both shorts share the same strike — use IRON_BUTTERFLY instead.
- For directional spreads, short strike should be near the defended level (bull put near support, bear call near resistance).
"""


def _is_iron_fly_strikes(sell_put: int | None, sell_call: int | None) -> bool:
    return sell_put is not None and sell_call is not None and sell_put == sell_call


def _is_iron_condor_strikes(sell_put: int | None, sell_call: int | None) -> bool:
    return sell_put is not None and sell_call is not None and sell_put != sell_call


def _reconcile_iron_label(result: StrategyRecommendation) -> None:
    """Fix LLM mislabels: same short strike = butterfly, different = condor."""
    if result.strategy not in ("IRON_CONDOR", "IRON_BUTTERFLY"):
        return

    sp, sc = result.sell_put_strike, result.sell_call_strike
    if result.strategy == "IRON_CONDOR" and _is_iron_fly_strikes(sp, sc):
        result.strategy = "IRON_BUTTERFLY"
        note = "Relabeled from IRON_CONDOR: both shorts are at the same strike (iron butterfly, not condor)."
        result.structure_note = note
        result.conflicting_signals = list(result.conflicting_signals or []) + [note]
    elif result.strategy == "IRON_BUTTERFLY" and _is_iron_condor_strikes(sp, sc):
        result.strategy = "IRON_CONDOR"
        note = "Relabeled from IRON_BUTTERFLY: short put and short call are at different strikes (iron condor)."
        result.structure_note = note
        result.conflicting_signals = list(result.conflicting_signals or []) + [note]


def _normalise_iron_four_leg(
    result: StrategyRecommendation,
    greeks: GreeksData,
    chain,
    lot_multiplier: float,
    *,
    same_short_strike: bool,
) -> StrategyRecommendation:
    from config import STRIKE_INTERVAL

    if same_short_strike:
        body = result.sell_put_strike or result.sell_call_strike or greeks.atm_strike
        result.sell_put_strike = body
        result.sell_call_strike = body
        result.buy_put_strike = result.buy_put_strike or body - STRIKE_INTERVAL
        result.buy_call_strike = result.buy_call_strike or body + STRIKE_INTERVAL
    else:
        result.sell_put_strike = result.sell_put_strike or greeks.sell_put_strike
        result.buy_put_strike = result.buy_put_strike or result.sell_put_strike - STRIKE_INTERVAL
        result.sell_call_strike = result.sell_call_strike or greeks.sell_call_strike
        result.buy_call_strike = result.buy_call_strike or result.sell_call_strike + STRIKE_INTERVAL

    result.sell_put_premium = _chain_premium(chain, result.sell_put_strike, "put") or greeks.sell_put.ltp
    result.buy_put_premium = _chain_premium(chain, result.buy_put_strike, "put") or max(
        (result.sell_put_premium or 0) * 0.4, 1.0
    )
    result.sell_call_premium = _chain_premium(chain, result.sell_call_strike, "call") or greeks.sell_call.ltp
    result.buy_call_premium = _chain_premium(chain, result.buy_call_strike, "call") or max(
        (result.sell_call_premium or 0) * 0.4, 1.0
    )
    net = (result.sell_put_premium - result.buy_put_premium) + (
        result.sell_call_premium - result.buy_call_premium
    )
    spread = max(
        (result.sell_put_strike or 0) - (result.buy_put_strike or 0),
        (result.buy_call_strike or 0) - (result.sell_call_strike or 0),
    )
    result.net_premium = round(net, 2)
    result.max_profit = round(net * lot_multiplier, 2)
    result.max_loss = round((spread - net) * lot_multiplier, 2)
    result.lower_breakeven = round((result.sell_put_strike or 0) - net, 2)
    result.upper_breakeven = round((result.sell_call_strike or 0) + net, 2)
    return result


def _apply_expiry_day_guards(
    result: StrategyRecommendation,
    chain,
    greeks: GreeksData,
) -> None:
    expiry = str(getattr(chain, "attrs", {}).get("expiry") or "") if chain is not None else ""
    if not is_expiry_day(expiry):
        return

    result.is_expiry_day = True
    result.days_to_expiry = 0
    warn = "0 DTE (weekly expiry day): elevated gamma/pin risk for short premium structures."
    result.conflicting_signals = list(result.conflicting_signals or []) + [warn]

    if result.strategy == "IRON_BUTTERFLY":
        _override_to_wait(
            result,
            "Iron butterfly on 0 DTE is not permitted (extreme gamma); use next-week expiry or iron condor.",
            True,
        )
        return
    elif result.strategy == "IRON_CONDOR":
        condor_note = "Expiry-day iron condor: prefer wings outside expected intraday range; watch pin at max pain."
        result.structure_note = (result.structure_note + " | " if result.structure_note else "") + condor_note
        if result.confidence == "HIGH":
            result.confidence = "MEDIUM"
    elif result.strategy == "SHORT_STRANGLE":
        if result.confidence != "LOW":
            result.confidence = "LOW"
        _override_to_wait(
            result,
            "Short strangle on 0 DTE is not permitted by guardrails (unlimited risk into expiry).",
            True,
        )


def _apply_time_guards(result: StrategyRecommendation, chain) -> None:
    """Block new short premium on 0 DTE after entry cutoff."""
    if result.strategy == "WAIT":
        return
    expiry = str(getattr(chain, "attrs", {}).get("expiry") or "") if chain is not None else ""
    if is_past_0dte_entry_cutoff(expiry):
        _override_to_wait(
            result,
            "Past 0 DTE entry cutoff (14:30 IST) — no new short premium on today's expiry.",
            True,
        )


def _build_context(
    market: MarketData,
    technical: TechnicalData,
    oi: OIAnalysis,
    greeks: GreeksData,
    chain,
) -> str:
    expiry = ""
    ch_src = "unknown"
    if chain is not None and not getattr(chain, "empty", True):
        expiry = str(getattr(chain, "attrs", {}).get("expiry") or "")
        ch_src = str(getattr(chain, "attrs", {}).get("source") or "unknown")

    ist_label, phase = session_phase_with_expiry(expiry)
    expiry_block = expiry_context_block(expiry)

    mq, cq, issues = data_integrity_status(market, chain)
    ok_line = "YES" if (mq and cq) else "NO"
    issues_txt = "; ".join(issues) if issues else "none"

    pos = positioning_bullets(market.nifty_spot, technical.atr, oi)
    skew = iv_skew_line(greeks)
    strip = format_atm_strip(chain, greeks.atm_strike, width=5)
    legs_tbl = format_suggested_legs_with_chain(chain, greeks)
    timing = entry_timing_block(expiry)
    intra = intraday_context(market)
    top_oi = format_top_oi_strikes(oi)
    dual = dual_expiry_summary(OpenAlgoService(), market.nifty_spot, expiry) if mq and cq else ""
    move_lbl = greeks.expected_move_method or "unknown"

    return f"""
{reliability_legend()}

DATA INTEGRITY (ground truth — do not contradict):
- Snapshot time: {ist_label} | Session phase: {phase}
- Index quote live: {mq} | data_source={getattr(market, 'data_source', 'unknown')} | is_mock={getattr(market, 'is_mock', True)}
- Option chain rows: {0 if chain is None or getattr(chain,'empty',True) else len(chain)} | chain_source={ch_src}
{expiry_block}
{timing}
- TRADE_READY (live quote AND live chain): {ok_line}
- Issues: {issues_txt}

RULE: If TRADE_READY is NO, you MUST recommend strategy=WAIT only. Explain briefly in wait_reason.
RULE: If Entry window says BLOCKED, you MUST recommend WAIT.
RULE: On 0 DTE, prefer WAIT or state that trades should use NEXT expiry (see NEXT EXPIRY section).

MARKET SNAPSHOT [LIVE]:
- Nifty Spot: {market.nifty_spot}
- VIX: {market.vix} | Change: {market.change_pct:+.2f}%
- Today: O={market.today_open} H={market.today_high} L={market.today_low} PrevClose={market.prev_close}

INTRADAY [ESTIMATED]:
{intra}

TECHNICAL ANALYSIS [ESTIMATED — rule-based trend label]:
- Trend: {technical.trend}
- RSI(14): {technical.rsi} → {technical.rsi_signal}
- MACD Line: {technical.macd_line} → {technical.macd_signal_text}
- SMA20: {technical.sma_20} | SMA50: {technical.sma_50} | ATR: {technical.atr}
- Bollinger Width: {technical.bb_width} (lower = more compressed)

POSITION vs LEVELS:
{pos}
{skew}

OI ANALYSIS:
- Support [ESTIMATED]: {oi.support} (Put OI: {oi.support_put_oi:,.0f})
- Resistance [ESTIMATED]: {oi.resistance} (Call OI: {oi.resistance_call_oi:,.0f})
- PCR {oi.pcr} → {oi.pcr_sentiment} [ESTIMATED — scope: {oi.pcr_scope}]
- Max Pain [ESTIMATED — scope: {oi.max_pain_scope}]: {oi.max_pain} (Distance from spot: {oi.max_pain_distance:+.0f})
- VIX Percentile (IV Rank): {oi.iv_rank:.1f}% → {oi.iv_environment} [method: {oi.iv_rank_method}]
- Trading Range: {oi.range_width:.0f} pts ({oi.range_width_pct:.1f}%)

{top_oi}

OPTIONS GREEKS (use CHAIN TABLE for trade prices):
- ATM Strike: {greeks.atm_strike} | Blended ATM IV: {greeks.atm_iv}%
- Implied move (ATM straddle): ±{greeks.expected_daily_move:.0f} pts [method: {move_lbl}]
- Greeks model source: {greeks.greeks_source}

SUGGESTED STRUCTURE LEGS [LIVE quotes — prefer strikes listed here]:
{legs_tbl}

ATM STRIP [LIVE] (± strikes, CE/PE bid-mid-ask):
{strip}
{dual}
"""


def _chain_premium(chain, strike: int | None, option_type: str) -> float | None:
    if chain is None or strike is None or getattr(chain, "empty", True):
        return None

    row = chain[chain["strike"] == strike]
    if row.empty:
        row = chain.iloc[(chain["strike"] - strike).abs().argsort()[:1]]

    prefix = "put" if option_type == "put" else "call"
    try:
        return round(float(row.iloc[0][f"{prefix}_ltp"]), 2)
    except (KeyError, TypeError, ValueError):
        return None


def _lot_size(chain) -> int:
    if chain is not None and "lot_size" in chain.columns and not chain["lot_size"].empty:
        try:
            return int(chain["lot_size"].mode().iloc[0])
        except (IndexError, TypeError, ValueError):
            pass

    from config import LOT_SIZE

    return LOT_SIZE


def _row_for_strike(chain, strike: int | None):
    if chain is None or strike is None or getattr(chain, "empty", True):
        return None
    row = chain[chain["strike"] == strike]
    if row.empty:
        row = chain.iloc[(chain["strike"] - strike).abs().argsort()[:1]]
    return row.iloc[0]


def _px_short(row, prefix: str) -> float:
    if row is None:
        return 0.0
    bid = float(row.get(f"{prefix}_bid", 0) or 0)
    ltp = float(row.get(f"{prefix}_ltp", 0) or 0)
    return bid if bid > 0 else ltp


def _px_long(row, prefix: str) -> float:
    if row is None:
        return 0.0
    ask = float(row.get(f"{prefix}_ask", 0) or 0)
    ltp = float(row.get(f"{prefix}_ltp", 0) or 0)
    return ask if ask > 0 else ltp


def _apply_conservative_financials(result: StrategyRecommendation, chain, lot_multiplier: float) -> None:
    """Worse case: collect shorts at bid, pay longs at ask (paper realism)."""
    if result.strategy == "WAIT" or chain is None or getattr(chain, "empty", True):
        return

    if result.strategy == "BULL_PUT_SPREAD":
        sp = _row_for_strike(chain, result.sell_put_strike)
        bp = _row_for_strike(chain, result.buy_put_strike)
        a = _px_short(sp, "put")
        b = _px_long(bp, "put")
        net = round(a - b, 2)
        spread = (result.sell_put_strike or 0) - (result.buy_put_strike or 0)
        result.conservative_net_premium = net
        result.conservative_max_profit = round(net * lot_multiplier, 2)
        result.conservative_max_loss = round((spread - net) * lot_multiplier, 2)
        result.conservative_lower_breakeven = round((result.sell_put_strike or 0) - net, 2)
        return

    if result.strategy == "BEAR_CALL_SPREAD":
        sc = _row_for_strike(chain, result.sell_call_strike)
        bc = _row_for_strike(chain, result.buy_call_strike)
        net = round(_px_short(sc, "call") - _px_long(bc, "call"), 2)
        spread = (result.buy_call_strike or 0) - (result.sell_call_strike or 0)
        result.conservative_net_premium = net
        result.conservative_max_profit = round(net * lot_multiplier, 2)
        result.conservative_max_loss = round((spread - net) * lot_multiplier, 2)
        result.conservative_upper_breakeven = round((result.sell_call_strike or 0) + net, 2)
        return

    if result.strategy in ("IRON_CONDOR", "IRON_BUTTERFLY"):
        sp = _row_for_strike(chain, result.sell_put_strike)
        bp = _row_for_strike(chain, result.buy_put_strike)
        sc = _row_for_strike(chain, result.sell_call_strike)
        bc = _row_for_strike(chain, result.buy_call_strike)
        net = (_px_short(sp, "put") - _px_long(bp, "put")) + (_px_short(sc, "call") - _px_long(bc, "call"))
        net = round(net, 2)
        spread = max(
            (result.sell_put_strike or 0) - (result.buy_put_strike or 0),
            (result.buy_call_strike or 0) - (result.sell_call_strike or 0),
        )
        result.conservative_net_premium = net
        result.conservative_max_profit = round(net * lot_multiplier, 2)
        result.conservative_max_loss = round((spread - net) * lot_multiplier, 2)
        result.conservative_lower_breakeven = round((result.sell_put_strike or 0) - net, 2)
        result.conservative_upper_breakeven = round((result.sell_call_strike or 0) + net, 2)
        return

    if result.strategy == "SHORT_STRANGLE":
        sp = _row_for_strike(chain, result.sell_put_strike)
        sc = _row_for_strike(chain, result.sell_call_strike)
        net = round(_px_short(sp, "put") + _px_short(sc, "call"), 2)
        result.conservative_net_premium = net
        result.conservative_max_profit = round(net * lot_multiplier, 2)
        result.conservative_max_loss = None
        result.conservative_lower_breakeven = round((result.sell_put_strike or 0) - net, 2)
        result.conservative_upper_breakeven = round((result.sell_call_strike or 0) + net, 2)


def _stamp_integrity_meta(result: StrategyRecommendation, market: MarketData, chain) -> None:
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
    if issues:
        result.integrity_note = "; ".join(issues)
    else:
        result.integrity_note = None


def _override_to_wait(result: StrategyRecommendation, reason: str, had_model_trade: bool) -> None:
    prev = result.strategy
    extras = ""
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


def _maybe_apply_trade_guards(result: StrategyRecommendation, market: MarketData, chain) -> None:
    if result.strategy != "WAIT" and not result.data_trade_ready:
        _override_to_wait(result, "Live quote and/or live option chain required for actionable trades.", True)
        return
    if result.strategy != "WAIT":
        ok, msg = strikes_valid_for_strategy(
            chain,
            result.strategy,
            result.sell_put_strike,
            result.buy_put_strike,
            result.sell_call_strike,
            result.buy_call_strike,
        )
        if not ok:
            _override_to_wait(result, f"Strike validation failed: {msg}", True)


def _normalise_financials(result: StrategyRecommendation, greeks: GreeksData, chain) -> StrategyRecommendation:
    from config import NUM_LOTS, STRIKE_INTERVAL

    lot_multiplier = _lot_size(chain) * NUM_LOTS

    if result.strategy == "WAIT":
        result.sell_put_strike = result.buy_put_strike = None
        result.sell_call_strike = result.buy_call_strike = None
        result.sell_put_premium = result.buy_put_premium = None
        result.sell_call_premium = result.buy_call_premium = None
        result.net_premium = result.max_profit = result.max_loss = None
        result.lower_breakeven = result.upper_breakeven = None
        return result

    if result.strategy == "BULL_PUT_SPREAD":
        result.sell_put_strike = result.sell_put_strike or greeks.sell_put_strike
        result.buy_put_strike = result.buy_put_strike or result.sell_put_strike - STRIKE_INTERVAL
        result.sell_call_strike = result.buy_call_strike = None
        result.sell_put_premium = _chain_premium(chain, result.sell_put_strike, "put") or greeks.sell_put.ltp
        result.buy_put_premium = _chain_premium(chain, result.buy_put_strike, "put") or max(result.sell_put_premium * 0.4, 1.0)
        result.sell_call_premium = result.buy_call_premium = None
        net = result.sell_put_premium - result.buy_put_premium
        spread = result.sell_put_strike - result.buy_put_strike
        result.net_premium = round(net, 2)
        result.max_profit = round(net * lot_multiplier, 2)
        result.max_loss = round((spread - net) * lot_multiplier, 2)
        result.lower_breakeven = round(result.sell_put_strike - net, 2)
        result.upper_breakeven = None
        return result

    if result.strategy == "BEAR_CALL_SPREAD":
        result.sell_call_strike = result.sell_call_strike or greeks.sell_call_strike
        result.buy_call_strike = result.buy_call_strike or result.sell_call_strike + STRIKE_INTERVAL
        result.sell_put_strike = result.buy_put_strike = None
        result.sell_call_premium = _chain_premium(chain, result.sell_call_strike, "call") or greeks.sell_call.ltp
        result.buy_call_premium = _chain_premium(chain, result.buy_call_strike, "call") or max(result.sell_call_premium * 0.4, 1.0)
        result.sell_put_premium = result.buy_put_premium = None
        net = result.sell_call_premium - result.buy_call_premium
        spread = result.buy_call_strike - result.sell_call_strike
        result.net_premium = round(net, 2)
        result.max_profit = round(net * lot_multiplier, 2)
        result.max_loss = round((spread - net) * lot_multiplier, 2)
        result.lower_breakeven = None
        result.upper_breakeven = round(result.sell_call_strike + net, 2)
        return result

    if result.strategy == "IRON_CONDOR":
        return _normalise_iron_four_leg(
            result, greeks, chain, lot_multiplier, same_short_strike=False
        )

    if result.strategy == "IRON_BUTTERFLY":
        return _normalise_iron_four_leg(
            result, greeks, chain, lot_multiplier, same_short_strike=True
        )

    if result.strategy == "SHORT_STRANGLE":
        result.sell_put_strike = result.sell_put_strike or greeks.sell_put_strike
        result.sell_call_strike = result.sell_call_strike or greeks.sell_call_strike
        result.buy_put_strike = result.buy_call_strike = None
        result.sell_put_premium = _chain_premium(chain, result.sell_put_strike, "put") or greeks.sell_put.ltp
        result.sell_call_premium = _chain_premium(chain, result.sell_call_strike, "call") or greeks.sell_call.ltp
        result.buy_put_premium = result.buy_call_premium = None
        net = result.sell_put_premium + result.sell_call_premium
        result.net_premium = round(net, 2)
        result.max_profit = round(net * lot_multiplier, 2)
        result.max_loss = None
        result.lower_breakeven = round(result.sell_put_strike - net, 2)
        result.upper_breakeven = round(result.sell_call_strike + net, 2)
        return result

    return result


def strategy_agent(
    market: MarketData,
    technical: TechnicalData,
    oi: OIAnalysis,
    greeks: GreeksData,
    chain=None,
) -> StrategyRecommendation:
    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0.0,
    )

    structured_llm = llm.with_structured_output(StrategyRecommendation)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You are an expert Nifty options trader and strategist.\n"
            "Always respect the DATA INTEGRITY section: if TRADE_READY is NO, you MUST output strategy WAIT only "
            "and explain briefly in wait_reason.\n"
            "Do not invent strikes that contradict the suggested leg quotes / ATM strip; choose strikes listed there "
            "when possible.\n"
            "The numbered POSITION vs LEVELS and DATA INTEGRITY lines are factual—do not contradict them.\n\n"
            + STRATEGY_FRAMEWORK,
        ),
        (
            "human",
            "Based on the following market context, recommend the best options strategy.\n\n"
            "{context}\n\n"
            "Provide specific strikes from the tables above.\n"
            "Net premium in your reasoning is approximate (LTP-oriented); executable bid/ask are in the legs table.\n"
            "Use IRON_BUTTERFLY only when sell_put_strike equals sell_call_strike; use IRON_CONDOR when they differ.\n"
            "On 0 DTE / expiry day, state pin/gamma risk explicitly in reasoning.\n"
            "Be explicit about aligned vs conflicting signals.\n"
            "If WAIT, give a clear wait_reason.",
        ),
    ])

    context = _build_context(market, technical, oi, greeks, chain)
    llm_chain = prompt | structured_llm
    result = llm_chain.invoke({"context": context})

    _reconcile_iron_label(result)
    result = _normalise_financials(result, greeks, chain)
    _reconcile_iron_label(result)
    lot_multiplier = _lot_size(chain) * NUM_LOTS
    _apply_conservative_financials(result, chain, float(lot_multiplier))
    _stamp_integrity_meta(result, market, chain)
    _maybe_apply_trade_guards(result, market, chain)
    _apply_expiry_day_guards(result, chain, greeks)
    _apply_time_guards(result, chain)

    return result
