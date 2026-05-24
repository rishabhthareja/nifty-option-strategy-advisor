"""Phase 1B/1C: strike candidates with POP and reward-risk scoring."""

from __future__ import annotations

import math
from typing import Optional, Tuple

import pandas as pd
from scipy.stats import norm

from config import (
    MIN_EST_POP_PCT,
    MIN_REWARD_RISK,
    MIN_SHORT_MOVE_MULT,
    NUM_LOTS,
    POP_DELTA_MAX,
    POP_DELTA_MIN,
    POP_DELTA_TARGET,
    POP_SCORE_WEIGHT,
    RISK_FREE_RATE,
    RR_SCORE_WEIGHT,
    SHORT_DELTA_MAX,
    SHORT_DELTA_MIN,
    SHORT_DELTA_TARGET,
    STRIKE_INTERVAL,
)
from models.market import MarketData, TechnicalData
from models.options import GreeksData, OIAnalysis, StrikeCandidate
from services.expiry_utils import days_to_expiry


def _row(chain: pd.DataFrame, strike: int) -> pd.Series:
    row = chain[chain["strike"] == strike]
    if row.empty:
        row = chain.iloc[(chain["strike"] - strike).abs().argsort()[:1]]
    return row.iloc[0]


def _ltp(chain: pd.DataFrame, strike: int, side: str) -> float:
    prefix = "put" if side == "put" else "call"
    try:
        return round(float(_row(chain, strike)[f"{prefix}_ltp"]), 2)
    except (KeyError, TypeError, ValueError):
        return 0.0


def _delta(chain: pd.DataFrame, strike: int, side: str) -> Optional[float]:
    prefix = "put" if side == "put" else "call"
    try:
        return round(float(_row(chain, strike)[f"{prefix}_delta"]), 4)
    except (KeyError, TypeError, ValueError):
        return None


def _lot_multiplier(chain: pd.DataFrame) -> float:
    from config import LOT_SIZE

    if "lot_size" in chain.columns and not chain["lot_size"].empty:
        try:
            raw = int(chain["lot_size"].mode().iloc[0])
            if raw > 0:
                return raw * NUM_LOTS
        except (IndexError, TypeError, ValueError):
            pass
    return LOT_SIZE * NUM_LOTS


def _find_strike_near_delta(
    chain: pd.DataFrame,
    spot: float,
    side: str,
    otm_only: bool = True,
    *,
    target: float = SHORT_DELTA_TARGET,
    dmin: float = SHORT_DELTA_MIN,
    dmax: float = SHORT_DELTA_MAX,
) -> Optional[int]:
    prefix = "put" if side == "put" else "call"
    best_strike: Optional[int] = None
    best_dist = 1e9

    for strike in sorted(chain["strike"].astype(int).unique()):
        strike = int(strike)
        if otm_only:
            if side == "put" and strike >= spot:
                continue
            if side == "call" and strike <= spot:
                continue
        d = _delta(chain, strike, side)
        if d is None:
            continue
        mag = abs(d)
        if mag < dmin or mag > dmax:
            continue
        dist = abs(mag - target)
        if dist < best_dist:
            best_dist = dist
            best_strike = strike
    return best_strike


def _iron_condor_financials(
    chain: pd.DataFrame,
    sp: int,
    bp: int,
    sc: int,
    bc: int,
) -> tuple[float, float, float, float, float, int, int]:
    lot_mult = _lot_multiplier(chain)
    sp_p = _ltp(chain, sp, "put")
    bp_p = _ltp(chain, bp, "put")
    sc_p = _ltp(chain, sc, "call")
    bc_p = _ltp(chain, bc, "call")
    net = round((sp_p - bp_p) + (sc_p - bc_p), 2)
    put_wing = sp - bp
    call_wing = bc - sc
    spread = max(put_wing, call_wing)
    max_profit = round(net * lot_mult, 2)
    max_loss = round((spread - net) * lot_mult, 2)
    lower_be = round(sp - net, 2)
    upper_be = round(sc + net, 2)
    return net, max_profit, max_loss, lower_be, upper_be, put_wing, call_wing


def _spread_financials(
    chain: pd.DataFrame, sell: int, buy: int, side: str
) -> tuple[float, float, float, float, Optional[float]]:
    lot_mult = _lot_multiplier(chain)
    sell_p = _ltp(chain, sell, side)
    buy_p = _ltp(chain, buy, side)
    net = round(sell_p - buy_p, 2)
    if side == "put":
        spread = sell - buy
        lower_be = round(sell - net, 2)
        return net, round(net * lot_mult, 2), round((spread - net) * lot_mult, 2), lower_be, None
    spread = buy - sell
    upper_be = round(sell + net, 2)
    return net, round(net * lot_mult, 2), round((spread - net) * lot_mult, 2), None, upper_be


def _reward_risk(max_profit: float, max_loss: float) -> float:
    if max_loss <= 0 or max_profit <= 0:
        return 0.0
    return round(max_profit / max_loss, 2)


def _estimate_pop_log_normal(
    spot: float,
    lower_be: float,
    upper_be: float,
    atm_iv: float,
    dte: int,
    r: float = RISK_FREE_RATE,
) -> Optional[float]:
    """P(lower_be < S_T < upper_be) under log-normal (risk-neutral d2). Returns POP % or None."""
    if upper_be <= lower_be or spot <= 0 or atm_iv <= 0 or dte <= 0:
        return None
    sigma = atm_iv / 100.0
    t_years = dte / 365.0
    vol_sqrt_t = sigma * math.sqrt(t_years)
    if vol_sqrt_t <= 0:
        return None
    drift = (r - 0.5 * sigma * sigma) * t_years
    d2_lower = (math.log(spot / lower_be) + drift) / vol_sqrt_t
    d2_upper = (math.log(spot / upper_be) + drift) / vol_sqrt_t
    pop = (norm.cdf(d2_lower) - norm.cdf(d2_upper)) * 100.0
    return round(min(90.0, max(10.0, pop)), 1)


def _estimate_pop_delta_heuristic(
    spot: float,
    lower_be: Optional[float],
    upper_be: Optional[float],
    short_put_delta: Optional[float],
    short_call_delta: Optional[float],
    strategy: str,
) -> float:
    """Legacy POP: OTM short deltas × independence + optional centering tweak."""
    if strategy in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"):
        d = short_put_delta if strategy == "BULL_PUT_SPREAD" else short_call_delta
        if d is None:
            return 55.0
        return round(min(88.0, max(45.0, (1 - abs(d)) * 100)), 1)

    p_put = 1 - min(abs(short_put_delta or 0.3), 0.95)
    p_call = 1 - min(abs(short_call_delta or 0.3), 0.95)
    base = p_put * p_call * 100
    if lower_be is not None and upper_be is not None and upper_be > lower_be:
        center_pct = (spot - lower_be) / (upper_be - lower_be) * 100
        off_center = abs(center_pct - 50) / 50
        base *= 1 - 0.2 * min(1.0, off_center)
    return round(min(90.0, max(10.0, base)), 1)


def _estimate_pop_pct(
    spot: float,
    lower_be: Optional[float],
    upper_be: Optional[float],
    short_put_delta: Optional[float],
    short_call_delta: Optional[float],
    strategy: str,
    atm_iv: Optional[float] = None,
    dte: Optional[int] = None,
    r: float = RISK_FREE_RATE,
) -> Tuple[float, str]:
    """POP % and method label (log_normal or delta_heuristic)."""
    if (
        strategy == "IRON_CONDOR"
        and atm_iv is not None
        and dte is not None
        and lower_be is not None
        and upper_be is not None
    ):
        ln_pop = _estimate_pop_log_normal(spot, lower_be, upper_be, atm_iv, int(dte), r)
        if ln_pop is not None:
            return ln_pop, "log_normal"

    return (
        _estimate_pop_delta_heuristic(
            spot, lower_be, upper_be, short_put_delta, short_call_delta, strategy
        ),
        "delta_heuristic",
    )


def _condor_breakevens_from_shorts(
    chain: pd.DataFrame, sp: int, sc: int, wing: int = STRIKE_INTERVAL
) -> Tuple[Optional[float], Optional[float]]:
    """Approximate IC breakevens from short strikes + wing (for POP during pair scan)."""
    bp = sp - wing
    bc = sc + wing
    _, _, _, lower_be, upper_be, _, _ = _iron_condor_financials(chain, sp, bp, sc, bc)
    return lower_be, upper_be


def _composite_score(est_pop: float, reward_risk: float) -> float:
    rr_pts = min(reward_risk, 1.0) * 100
    return round(POP_SCORE_WEIGHT * est_pop + RR_SCORE_WEIGHT * rr_pts, 1)


def _vs_move_note(spot: float, sp: int, sc: int, implied_move: float) -> str:
    if implied_move <= 0:
        return "implied move n/a"
    put_dist = spot - sp
    call_dist = sc - spot
    return (
        f"short put {put_dist:.0f} pts below spot ({put_dist / implied_move:.2f}x move), "
        f"short call {call_dist:.0f} pts above ({call_dist / implied_move:.2f}x move)"
    )


def _enrich_metrics(
    c: StrikeCandidate,
    spot: float,
    implied_move: float,
    atm_iv: float,
    dte: Optional[int],
) -> StrikeCandidate:
    pop, method = _estimate_pop_pct(
        spot,
        c.lower_breakeven,
        c.upper_breakeven,
        c.short_put_delta,
        c.short_call_delta,
        c.strategy,
        atm_iv=atm_iv,
        dte=dte,
    )
    c.est_pop_pct = pop
    c.pop_method = method
    c.reward_risk = _reward_risk(c.max_profit_ltp, c.max_loss_ltp)
    c.composite_score = _composite_score(c.est_pop_pct, c.reward_risk)
    if implied_move > 0 and c.sell_put_strike and c.sell_call_strike:
        put_ok = (spot - c.sell_put_strike) >= implied_move * MIN_SHORT_MOVE_MULT
        call_ok = (c.sell_call_strike - spot) >= implied_move * MIN_SHORT_MOVE_MULT
        if not put_ok or not call_ok:
            c.composite_score = round((c.composite_score or 0) * 0.85, 1)
            c.notes += " | Short(s) inside min move cushion — score penalized."
    return c


def _build_iron_condor_candidate(
    chain: pd.DataFrame,
    market: MarketData,
    implied_move: float,
    atm_iv: float,
    dte: Optional[int],
    candidate_id: str,
    label: str,
    sp: int,
    sc: int,
    notes: str,
    *,
    wing: int = STRIKE_INTERVAL,
) -> StrikeCandidate:
    spot = float(market.nifty_spot)
    bp = sp - wing
    bc = sc + wing
    net, max_p, max_l, lo, hi, put_w, call_w = _iron_condor_financials(chain, sp, bp, sc, bc)
    c = StrikeCandidate(
        candidate_id=candidate_id,
        strategy="IRON_CONDOR",
        label=label,
        sell_put_strike=sp,
        buy_put_strike=bp,
        sell_call_strike=sc,
        buy_call_strike=bc,
        net_premium_ltp=net,
        max_profit_ltp=max_p,
        max_loss_ltp=max_l,
        lower_breakeven=lo,
        upper_breakeven=hi,
        short_put_delta=_delta(chain, sp, "put"),
        short_call_delta=_delta(chain, sc, "call"),
        put_distance_pts=round(spot - sp, 1),
        call_distance_pts=round(sc - spot, 1),
        put_wing_pts=put_w,
        call_wing_pts=call_w,
        vs_implied_move=_vs_move_note(spot, sp, sc, implied_move),
        notes=notes,
    )
    return _enrich_metrics(c, spot, implied_move, atm_iv, dte)


def _find_pop_condor_short_pair(
    chain: pd.DataFrame,
    spot: float,
    implied_move: float,
    atm_iv: float,
    dte: Optional[int],
) -> tuple[Optional[int], Optional[int]]:
    """Best POP-first short pair: ~0.17 delta and outside min move cushion."""
    best: tuple[Optional[int], Optional[int]] = (None, None)
    best_pop = -1.0
    for sp in sorted(chain["strike"].astype(int).unique()):
        if sp >= spot:
            continue
        for sc in sorted(chain["strike"].astype(int).unique()):
            if sc <= spot:
                continue
            if not _shorts_meet_move_cushion(spot, int(sp), int(sc), implied_move):
                continue
            d_put = _delta(chain, int(sp), "put")
            d_call = _delta(chain, int(sc), "call")
            if d_put is None or d_call is None:
                continue
            if not (POP_DELTA_MIN <= abs(d_put) <= POP_DELTA_MAX):
                continue
            if not (POP_DELTA_MIN <= abs(d_call) <= POP_DELTA_MAX):
                continue
            lower_be, upper_be = _condor_breakevens_from_shorts(chain, int(sp), int(sc))
            pop, _ = _estimate_pop_pct(
                spot,
                lower_be,
                upper_be,
                d_put,
                d_call,
                "IRON_CONDOR",
                atm_iv=atm_iv,
                dte=dte,
            )
            if pop > best_pop:
                best_pop = pop
                best = (int(sp), int(sc))
    return best


def _shorts_meet_move_cushion(
    spot: float, sp: int, sc: int, implied_move: float
) -> bool:
    if implied_move <= 0:
        return True
    return (spot - sp) >= implied_move * MIN_SHORT_MOVE_MULT and (
        sc - spot
    ) >= implied_move * MIN_SHORT_MOVE_MULT


def _mark_recommended(candidates: list[StrikeCandidate]) -> None:
    """Flag best iron condor by composite score that clears POP/R:R floors."""
    condors = [
        c
        for c in candidates
        if c.strategy == "IRON_CONDOR"
        and (c.est_pop_pct or 0) >= MIN_EST_POP_PCT
        and (c.reward_risk or 0) >= MIN_REWARD_RISK
    ]
    if not condors:
        condors = [c for c in candidates if c.strategy == "IRON_CONDOR"]
    if not condors:
        return
    best = max(condors, key=lambda c: (c.composite_score or 0, c.est_pop_pct or 0))
    for c in candidates:
        c.is_recommended = c.candidate_id == best.candidate_id
    if best.is_recommended:
        best.notes += " | RECOMMENDED (best POP/R:R balance)."


def recommended_candidate(
    candidates: list[StrikeCandidate], strategy: str = "IRON_CONDOR"
) -> StrikeCandidate | None:
    for c in candidates:
        if c.is_recommended and c.strategy == strategy:
            return c
    pool = [c for c in candidates if c.strategy == strategy]
    if not pool:
        return None
    return max(pool, key=lambda x: (x.composite_score or 0, x.est_pop_pct or 0))


def build_strike_candidates(
    chain: pd.DataFrame,
    market: MarketData,
    technical: TechnicalData,
    oi: OIAnalysis,
    greeks: GreeksData,
) -> list[StrikeCandidate]:
    if chain is None or getattr(chain, "empty", True):
        return []

    spot = float(market.nifty_spot)
    implied_move = float(greeks.expected_daily_move or 0)
    atm_iv = float(greeks.atm_iv or 0)
    expiry = str(getattr(chain, "attrs", {}).get("expiry") or "")
    dte = days_to_expiry(expiry)
    wing = STRIKE_INTERVAL
    candidates: list[StrikeCandidate] = []

    sp_a = greeks.sell_put_strike
    sc_a = greeks.sell_call_strike
    candidates.append(
        _build_iron_condor_candidate(
            chain,
            market,
            implied_move,
            atm_iv,
            dte,
            "A",
            "Iron condor (OI walls)",
            sp_a,
            sc_a,
            f"Short put support+{wing} ({oi.support}), short call resistance-{wing} ({oi.resistance}).",
        )
    )

    sp_b = int(oi.support)
    sc_b = sc_a
    if (sp_b, sc_b) != (sp_a, sc_a):
        candidates.append(
            _build_iron_condor_candidate(
                chain,
                market,
                implied_move,
                atm_iv,
                dte,
                "B",
                "Iron condor (wider put at support)",
                sp_b,
                sc_b,
                f"Short put at OI support {oi.support}; equal {wing} wings both sides.",
            )
        )

    dp, dc = _find_pop_condor_short_pair(chain, spot, implied_move, atm_iv, dte)
    if dp and dc:
        key_d = (dp, dc)
        existing = {(c.sell_put_strike, c.sell_call_strike) for c in candidates}
        if key_d not in existing:
            candidates.append(
                _build_iron_condor_candidate(
                    chain,
                    market,
                    implied_move,
                    atm_iv,
                    dte,
                    "D",
                    "Iron condor (POP-first ~0.17 delta)",
                    dp,
                    dc,
                    f"Shorts near {POP_DELTA_TARGET} delta; both outside {MIN_SHORT_MOVE_MULT}x implied move.",
                )
            )

    trend = (technical.trend or "").upper()
    if "UP" in trend or (technical.rsi > 52 and oi.pcr > 1.1):
        sp_c = int(oi.support)
        bp_c = sp_c - wing
        net, max_p, max_l, lo, hi = _spread_financials(chain, sp_c, bp_c, "put")
        c = StrikeCandidate(
            candidate_id="C",
            strategy="BULL_PUT_SPREAD",
            label="Bull put spread (support)",
            sell_put_strike=sp_c,
            buy_put_strike=bp_c,
            net_premium_ltp=net,
            max_profit_ltp=max_p,
            max_loss_ltp=max_l,
            lower_breakeven=lo,
            upper_breakeven=hi,
            short_put_delta=_delta(chain, sp_c, "put"),
            put_distance_pts=round(spot - sp_c, 1),
            vs_implied_move=f"short put {round(spot - sp_c, 0):.0f} pts below spot",
            notes=f"Mild bull: {trend}, PCR {oi.pcr:.2f}, defend {oi.support}.",
            put_wing_pts=wing,
        )
        candidates.append(_enrich_metrics(c, spot, implied_move, atm_iv, dte))
    elif "DOWN" in trend or (
        "BEARISH" in (technical.macd_signal_text or "").upper() and oi.pcr < 1.0
    ):
        sc_c = int(oi.resistance)
        bc_c = sc_c + wing
        net, max_p, max_l, lo, hi = _spread_financials(chain, sc_c, bc_c, "call")
        c = StrikeCandidate(
            candidate_id="C",
            strategy="BEAR_CALL_SPREAD",
            label="Bear call spread (resistance)",
            sell_call_strike=sc_c,
            buy_call_strike=bc_c,
            net_premium_ltp=net,
            max_profit_ltp=max_p,
            max_loss_ltp=max_l,
            lower_breakeven=lo,
            upper_breakeven=hi,
            short_call_delta=_delta(chain, sc_c, "call"),
            call_distance_pts=round(sc_c - spot, 1),
            vs_implied_move=f"short call {round(sc_c - spot, 0):.0f} pts above spot",
            notes=f"Mild bear: {trend}, MACD {technical.macd_signal_text}, cap {oi.resistance}.",
            call_wing_pts=wing,
        )
        candidates.append(_enrich_metrics(c, spot, implied_move, atm_iv, dte))

    seen: set[tuple] = set()
    unique: list[StrikeCandidate] = []
    for c in candidates:
        key = (
            c.strategy,
            c.sell_put_strike,
            c.buy_put_strike,
            c.sell_call_strike,
            c.buy_call_strike,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    _mark_recommended(unique)
    return sorted(
        unique,
        key=lambda c: (
            0 if c.is_recommended else 1,
            -(c.composite_score or 0),
            -(c.est_pop_pct or 0),
        ),
    )


def format_strike_candidates_table(candidates: list[StrikeCandidate]) -> str:
    if not candidates:
        return "- No strike candidates (empty chain)."

    rec = next((c.candidate_id for c in candidates if c.is_recommended), None)
    lines = [
        "STRIKE CANDIDATES (Phase 1C — pick one ID or WAIT):",
        "RULE: Prefer the row marked RECOMMENDED unless WAIT; it maximizes POP/R:R balance.",
        "RULE: est_POP = heuristic win probability; R:R = max_profit / max_loss. Floors: "
        f"POP >= {MIN_EST_POP_PCT:.0f}%, R:R >= {MIN_REWARD_RISK:.2f}.",
        "RULE: Use ONLY strikes from the chosen row. Equal wings (50/50) on all condor rows.",
        "",
        "| ID | Str | Net | est_POP% | R:R | Score | Rec | Short put | Short call | vs move |",
        "|----|-----|-----|----------|-----|-------|-----|-----------|------------|---------|",
    ]
    for c in candidates:
        sp = c.sell_put_strike or "—"
        sc = c.sell_call_strike or "—"
        pop = f"{c.est_pop_pct:.1f}" if c.est_pop_pct is not None else "—"
        rr = f"{c.reward_risk:.2f}" if c.reward_risk is not None else "—"
        score = f"{c.composite_score:.1f}" if c.composite_score is not None else "—"
        rec_mark = "YES" if c.is_recommended else ""
        lines.append(
            f"| {c.candidate_id} | {c.strategy[:8]} | {c.net_premium_ltp:.2f} | {pop} | {rr} | "
            f"{score} | {rec_mark} | {sp} | {sc} | {c.vs_implied_move[:40]} |"
        )
        wings = ""
        if c.put_wing_pts is not None and c.call_wing_pts is not None:
            wings = f" wings {c.put_wing_pts}/{c.call_wing_pts}"
        lines.append(f"  {c.label}{wings} — {c.notes}")
        if c.lower_breakeven is not None and c.upper_breakeven is not None:
            lines.append(
                f"  BE {c.lower_breakeven:.0f}–{c.upper_breakeven:.0f} | "
                f"profit ₹{c.max_profit_ltp:,.0f} | loss ₹{c.max_loss_ltp:,.0f}"
            )
    if rec:
        lines.append(f"\nSystem recommendation: candidate **{rec}** (highest composite POP/R:R score).")
    return "\n".join(lines)


def candidate_by_id(candidates: list[StrikeCandidate], cid: str | None) -> StrikeCandidate | None:
    if not cid:
        return None
    key = str(cid).strip().upper()
    for c in candidates:
        if c.candidate_id.upper() == key:
            return c
    return None
