"""Build rich factual context strings for strategist / evaluator prompts (tradeability)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore


def ist_now() -> datetime:
    """Current time in IST (approximate fixed offset UTC+5:30)."""
    from datetime import timezone, timedelta

    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist)


def session_phase_label(now: datetime | None = None) -> tuple[str, str]:
    """Return (ISO-like IST string ~ MM-DD HH:MM, phase label)."""
    dt = now or ist_now()
    label = dt.strftime("%Y-%m-%d %H:%M IST")
    mins = dt.hour * 60 + dt.minute
    open_m = 9 * 60 + 15
    close_m = 15 * 60 + 30
    if mins < open_m or mins > close_m:
        phase = "SESSION_CLOSED"
    elif mins < open_m + 75:
        phase = "OPENING_HOUR"
    elif mins >= close_m - 60:
        phase = "CLOSING_HOUR"
    else:
        phase = "REGULAR_SESSION"
    return label, phase


def _chain_strikes_set(chain) -> set[int]:
    if chain is None or getattr(chain, "empty", True) or pd is None:
        return set()
    try:
        return {int(x) for x in chain["strike"].unique()}
    except Exception:
        return set()


def data_integrity_status(market, chain) -> tuple[bool, bool, list[str]]:
    """Return (quotes_live, chain_live, issues). Both True => trade-ready chain+quote."""
    issues: list[str] = []

    mq = not getattr(market, "is_mock", True)
    if not mq:
        issues.append("Index quote is MOCK or fallback — not suitable for actionable trades.")

    cq = False
    if chain is None or getattr(chain, "empty", True):
        issues.append("Option chain unavailable or empty.")
    else:
        src = getattr(chain, "attrs", {}).get("source", "unknown")
        if src == "mock":
            issues.append(f"Option chain source is MOCK (attrs.source={src}).")
        else:
            cq = True

    return mq, cq, issues


def positioning_bullets(spot: float, atr: float, oi) -> str:
    """Precomputed distances — facts for LLM."""
    sup = getattr(oi, "support", 0)
    res = getattr(oi, "resistance", 0)
    mp = getattr(oi, "max_pain", 0)
    d_support = spot - float(sup)
    d_res = float(res) - spot
    d_mp = spot - float(mp)
    atr = float(atr) if atr else 0.0
    atr_note = ""
    if atr > 0:
        atr_note = (
            f" | vs ATR: support {d_support / atr:.2f} ATR away, "
            f"resistance {d_res / atr:.2f} ATR away, max pain {d_mp / atr:.2f} ATR"
        )
    return (
        f"- Spot to support ({sup}): {d_support:+.1f} pts ({d_support / spot * 100:.2f}% of spot){atr_note}\n"
        f"- Spot to resistance ({res}): {d_res:+.1f} pts ({d_res / spot * 100:.2f}% of spot)\n"
        f"- Spot vs max pain ({mp}): {d_mp:+.1f} pts\n"
    )


def iv_skew_line(greeks) -> str:
    try:
        ce = float(greeks.atm_call.iv)
        pe = float(greeks.atm_put.iv)
        skew = ce - pe
        return f"- ATM IV skew (CE - PE): {skew:+.2f} pp (CE {ce:.2f}% | PE {pe:.2f}%)"
    except Exception:
        return "- ATM IV skew: N/A"


def format_atm_strip(chain, atm_strike: int, width: int = 5) -> str:
    if chain is None or getattr(chain, "empty", True) or pd is None:
        return "(no chain rows)"
    try:
        sub = chain[(chain["strike"] >= atm_strike - width * 50) & (chain["strike"] <= atm_strike + width * 50)].copy()
        sub = sub.sort_values("strike")
        if sub.empty:
            idx = (chain["strike"] - atm_strike).abs().argsort()
            sub = chain.iloc[idx[: min(len(chain), width * 2 + 3)]]
        lines = ["strike | CE bid mid ask | PE bid mid ask | CE OI | PE OI"]
        for _, r in sub.iterrows():
            sb, sa = float(r.get("call_bid", 0) or 0), float(r.get("call_ask", 0) or 0)
            pb, pa = float(r.get("put_bid", 0) or 0), float(r.get("put_ask", 0) or 0)
            cm = (sb + sa) / 2 if sb and sa else float(r.get("call_ltp", 0) or 0)
            pm = (pb + pa) / 2 if pb and pa else float(r.get("put_ltp", 0) or 0)
            lines.append(
                f"{int(r['strike'])} | {sb:.1f}/{cm:.2f}/{sa:.1f} | {pb:.1f}/{pm:.2f}/{pa:.1f} | "
                f"{float(r.get('call_oi', 0) or 0):,.0f} | {float(r.get('put_oi', 0) or 0):,.0f}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"(strip error: {e})"


def leg_quote_line(chain, strike: int | None, side: str) -> str:
    """side is 'PUT' or 'CALL'."""
    if strike is None or chain is None or getattr(chain, "empty", True):
        return "(missing strike or chain)"
    row = chain[chain["strike"] == strike]
    if row.empty:
        row = chain.iloc[(chain["strike"] - strike).abs().argsort()[:1]]
    r = row.iloc[0]
    pref = "put" if side == "PUT" else "call"
    bid = float(r.get(f"{pref}_bid", 0) or 0)
    ask = float(r.get(f"{pref}_ask", 0) or 0)
    ltp = float(r.get(f"{pref}_ltp", 0) or 0)
    mid = ((bid + ask) / 2) if bid and ask else ltp
    oi = float(r.get(f"{pref}_oi", 0) or 0)
    return f"{strike} {side} | bid={bid:.2f} ask={ask:.2f} mid={mid:.2f} LTP={ltp:.2f} OI={oi:,.0f}"


def format_suggested_legs_with_chain(chain, greeks) -> str:
    rows = [
        f"SELL PUT  | {leg_quote_line(chain, greeks.sell_put_strike, 'PUT')}",
        f"BUY PUT   | {leg_quote_line(chain, greeks.buy_put_strike, 'PUT')}",
        f"SELL CALL | {leg_quote_line(chain, greeks.sell_call_strike, 'CALL')}",
        f"BUY CALL  | {leg_quote_line(chain, greeks.buy_call_strike, 'CALL')}",
    ]
    return "\n".join(rows)


def build_evaluator_digest_block(market, technical, oi, greeks, chain) -> str:
    """Second-opinion facts (must match strategist)."""
    from services.expiry_utils import expiry_context_block, session_phase_with_expiry
    from services.market_metrics import (
        entry_timing_block,
        format_top_oi_strikes,
        intraday_context,
        reliability_legend,
    )
    from services.openalgo_client import OpenAlgoService
    from services.market_metrics import weekly_expiry_context_block

    mq, cq, issues = data_integrity_status(market, chain)
    expiry = getattr(chain, "attrs", {}).get("expiry", "") if chain is not None else ""
    ist_label, phase = session_phase_with_expiry(expiry)
    ch_src = getattr(chain, "attrs", {}).get("source", "") if chain is not None else ""
    attrs = getattr(chain, "attrs", {}) or {} if chain is not None else {}
    session_cal = attrs.get("session_calendar_expiry", "")
    dual = ""
    if mq and cq and expiry:
        dual = weekly_expiry_context_block(
            OpenAlgoService(), market.nifty_spot, expiry, session_cal or None
        )

    move_note = (
        f"implied move ±{greeks.expected_daily_move:.0f} pts ({greeks.expected_move_method})"
    )
    lines = [
        f"Snapshot: {ist_label} | Phase: {phase}",
        f"TRADE_READY: {mq and cq} | quotes_live={mq} chain_live={cq} | issues: {'; '.join(issues) if issues else 'none'}",
        f"Chain: source={ch_src} expiry={expiry} rows={0 if chain is None or getattr(chain,'empty',True) else len(chain)}",
        expiry_context_block(expiry),
        entry_timing_block(expiry),
        "",
        intraday_context(market),
        "",
        "Position vs levels:",
        positioning_bullets(market.nifty_spot, technical.atr, oi).strip(),
        iv_skew_line(greeks),
        move_note,
        "",
        format_top_oi_strikes(oi),
        "",
        "Suggested leg quotes (reference A):",
        format_suggested_legs_with_chain(chain, greeks),
    ]
    from services.strike_candidates import format_strike_candidates_table

    cand_block = format_strike_candidates_table(getattr(greeks, "strike_candidates", None) or [])
    lines.extend(["", cand_block, "", "ATM strip (sample):", format_atm_strip(chain, greeks.atm_strike, width=4)])
    if dual:
        lines.extend(["", dual])
    lines.extend(["", reliability_legend()])
    return "\n".join(lines)


def strikes_valid_for_strategy(chain, strategy_name: str, sp=None, bp=None, sc=None, bc=None) -> tuple[bool, str]:
    if chain is None or getattr(chain, "empty", True):
        return False, "empty chain"
    strikes = _chain_strikes_set(chain)
    if strategy_name == "WAIT":
        return True, ""
    needed: list[int] = []
    if strategy_name == "BULL_PUT_SPREAD":
        needed = [x for x in (sp, bp) if x]
    elif strategy_name == "BEAR_CALL_SPREAD":
        needed = [x for x in (sc, bc) if x]
    elif strategy_name in ("IRON_CONDOR", "IRON_BUTTERFLY"):
        needed = [x for x in (sp, bp, sc, bc) if x]
    elif strategy_name == "SHORT_STRANGLE":
        needed = [x for x in (sp, sc) if x]
    if not needed:
        return False, "no strikes"
    missing = [s for s in needed if int(s) not in strikes]
    if missing:
        return False, f"strikes not in chain: {missing}; available samples {sorted(list(strikes))[:8]}..."
    return True, ""
