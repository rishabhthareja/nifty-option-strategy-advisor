"""Reliable market metrics for LLM prompts (VIX percentile, straddle move, intraday)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from config import STRIKE_INTERVAL
from services.expiry_utils import ist_now, parse_compact_expiry, days_to_expiry


def compute_vix_percentile(service, current_vix: float, lookback_days: int = 252) -> tuple[float, str]:
    """
    VIX percentile vs ~1Y daily INDIAVIX history.
    Returns (0-100 rank, method label).
    Falls back to widened static band if history unavailable.
    """
    try:
        hist = service.get_vix_ohlcv(days=lookback_days)
        if hist is not None and not hist.empty and "close" in hist.columns:
            closes = hist["close"].dropna()
            if len(closes) >= 20:
                lo = float(closes.min())
                hi = float(closes.max())
                if hi > lo:
                    pct = (current_vix - lo) / (hi - lo) * 100
                    return round(max(0.0, min(100.0, pct)), 1), "vix_percentile_1y"
    except Exception:
        pass

    # Fallback: wider static band than old 11–25 shortcut
    lo, hi = 10.0, 28.0
    pct = (current_vix - lo) / (hi - lo) * 100
    return round(max(0.0, min(100.0, pct)), 1), "vix_static_band_fallback"


def iv_environment_from_rank(iv_rank: float) -> str:
    if iv_rank >= 50:
        return "HIGH IV — Premium selling favoured"
    if iv_rank >= 30:
        return "MODERATE IV — Selective premium selling"
    return "LOW IV — Premium buying preferred"


def straddle_expected_move(chain, atm_strike: int, spot: float) -> tuple[float, float, str]:
    """
    Market-implied move from ATM straddle (call LTP + put LTP).
    Returns (half-width pts, full straddle price, method).
    """
    if chain is None or getattr(chain, "empty", True):
        return round(spot * 0.01, 2), 0.0, "unavailable"

    row = chain[chain["strike"] == atm_strike]
    if row.empty:
        row = chain.iloc[(chain["strike"] - atm_strike).abs().argsort()[:1]]

    r = row.iloc[0]
    call_px = float(r.get("call_ltp", 0) or 0)
    put_px = float(r.get("put_ltp", 0) or 0)
    straddle = call_px + put_px
    if straddle <= 0:
        return 0.0, 0.0, "unavailable"

    return round(straddle, 2), round(straddle, 2), "atm_straddle_ltp"


def intraday_context(market) -> str:
    """Spot vs today's range — factual intraday positioning."""
    spot = float(market.nifty_spot)
    o, h, l = float(market.today_open), float(market.today_high), float(market.today_low)
    day_range = h - l
    from_open = spot - o
    pos_in_range = ((spot - l) / day_range * 100) if day_range > 0 else 50.0

    lines = [
        f"- Spot vs today open: {from_open:+.1f} pts ({'above' if from_open > 0 else 'below' if from_open < 0 else 'at'} open)",
        f"- Today range: {l:.0f} – {h:.0f} ({day_range:.0f} pts) | Spot at {pos_in_range:.0f}% of range (0=low, 100=high)",
    ]
    if day_range > 0:
        used = abs(from_open) / day_range * 100 if day_range else 0
        lines.append(f"- Move from open as % of day range: {used:.0f}% [ESTIMATED intraday context]")
    return "\n".join(lines)


def format_top_oi_strikes(oi) -> str:
    puts = getattr(oi, "top_put_strikes", []) or []
    calls = getattr(oi, "top_call_strikes", []) or []
    put_lines = ", ".join(f"{p['strike']} ({p['oi']:,.0f})" for p in puts[:5]) or "n/a"
    call_lines = ", ".join(f"{c['strike']} ({c['oi']:,.0f})" for c in calls[:5]) or "n/a"
    return (
        f"- Top put OI strikes (loaded chain): {put_lines}\n"
        f"- Top call OI strikes (loaded chain): {call_lines}"
    )


def entry_timing_block(expiry: str | None) -> str:
    """When new short-premium entries are allowed."""
    now = ist_now()
    mins = now.hour * 60 + now.minute
    open_m = 9 * 60 + 45  # avoid first 30 min
    from config import ENTRY_CUTOFF_HOUR, ENTRY_CUTOFF_MINUTE

    cutoff_m = ENTRY_CUTOFF_HOUR * 60 + ENTRY_CUTOFF_MINUTE
    on_0_dte = days_to_expiry(expiry, now) == 0

    if mins < open_m:
        return "- Entry window: WAIT — before 09:45 IST (opening volatility)."
    if on_0_dte and mins >= cutoff_m:
        return (
            f"- Entry window: BLOCKED for new short premium on 0 DTE after "
            f"{ENTRY_CUTOFF_HOUR:02d}:{ENTRY_CUTOFF_MINUTE:02d} IST — prefer WAIT or next-week expiry only."
        )
    if on_0_dte:
        return (
            f"- Entry window: CAUTION — 0 DTE; new short premium only before "
            f"{ENTRY_CUTOFF_HOUR:02d}:{ENTRY_CUTOFF_MINUTE:02d} IST; prefer next-week expiry for iron condors."
        )
    if mins >= 15 * 60 + 0:
        return "- Entry window: CAUTION — after 15:00 IST; avoid new multi-leg entries."
    return "- Entry window: OK — regular session (prefer entries 09:45–14:30 IST)."


def weekly_expiry_context_block(
    service,
    spot: float,
    chain_expiry: str,
    session_calendar_expiry: str | None = None,
) -> str:
    """Always-on weekly expiry context: calendar vs active chain (live analysis)."""
    from config import MIN_ENTRY_DTE, PREFERRED_DTE_MAX, PREFERRED_DTE_MIN
    from services.expiry_utils import (
        nifty_next_weekly_expiry,
        nifty_weekly_expiry_for_session,
        parse_compact_expiry,
    )

    now = ist_now()
    session = session_calendar_expiry or nifty_weekly_expiry_for_session(now)
    next_exp = nifty_next_weekly_expiry(now)
    chain_dte = days_to_expiry(chain_expiry, now)
    session_dte = days_to_expiry(session, now)
    rolled = (
        parse_compact_expiry(session) is not None
        and parse_compact_expiry(chain_expiry) is not None
        and parse_compact_expiry(session) != parse_compact_expiry(chain_expiry)
    )

    lines = [
        "WEEKLY ENTRY EXPIRY (live):",
        f"- Session calendar expiry: {session} (DTE {session_dte if session_dte is not None else 'unknown'})",
        f"- Active analysis chain expiry: {chain_expiry} (DTE {chain_dte if chain_dte is not None else 'unknown'})",
        (
            f"- Policy: new weekly-style entries require DTE >= {MIN_ENTRY_DTE}; "
            f"preferred {PREFERRED_DTE_MIN}-{PREFERRED_DTE_MAX} DTE."
        ),
    ]
    if rolled:
        lines.append(
            "- Rolled forward: analysis chain is not the calendar expiry because the session week is too close."
        )
    else:
        lines.append("- Active chain matches calendar weekly target (no roll).")

    other = next_exp
    if parse_compact_expiry(other) != parse_compact_expiry(chain_expiry):
        try:
            nchain = service.get_options_chain(expiry=other)
            if nchain is not None and not getattr(nchain, "empty", True):
                atm = int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL)
                half_move, straddle, _ = straddle_expected_move(nchain, atm, spot)
                ne = getattr(nchain, "attrs", {}).get("expiry", other)
                lines.append(
                    f"- Alternate weekly {ne}: ATM straddle ≈ {straddle:.1f} pts "
                    f"(implied ±{half_move:.0f} pts) [LIVE]"
                )
            else:
                lines.append(f"- Alternate weekly {other}: chain unavailable")
        except Exception as e:
            lines.append(f"- Alternate weekly {other}: load failed ({e})")

    return "\n".join(lines)


def dual_expiry_summary(service, spot: float, primary_expiry: str) -> str:
    """Backward-compatible alias for weekly_expiry_context_block."""
    return weekly_expiry_context_block(service, spot, primary_expiry)


def reliability_legend() -> str:
    return (
        "FIELD RELIABILITY:\n"
        "- [LIVE] Nifty spot, VIX, option bid/ask/LTP/OI in chain tables\n"
        "- [ESTIMATED] Support/resistance (max OI heuristic), max pain (loaded strikes only), "
        "PCR (loaded chain only), trend label (rule-based)\n"
        "- [MODEL] VIX percentile from 1Y history (or static fallback), straddle implied move"
    )
