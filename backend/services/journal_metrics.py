"""Derived journal fields for session logs (paper trading / run replay)."""

from __future__ import annotations

from typing import Any, Optional

from config import MIN_EST_POP_PCT, MIN_REWARD_RISK


def bb_width_signal(bb_width: float) -> str:
    if bb_width < 0.04:
        return "TIGHT"
    if bb_width < 0.055:
        return "MODERATE"
    return "WIDE"


def range_position_metrics(spot: float, support: int, resistance: int) -> dict[str, Any]:
    """Distance to OI walls and position within support–resistance range."""
    spot_f = float(spot)
    sup = int(support)
    res = int(resistance)
    to_res = round(res - spot_f, 1)
    to_sup = round(spot_f - sup, 1)
    width = res - sup
    util_pct = round((spot_f - sup) / width * 100, 1) if width > 0 else None

    if util_pct is None:
        pos_label = "UNKNOWN"
    elif util_pct >= 85:
        pos_label = "NEAR_RESISTANCE"
    elif util_pct <= 15:
        pos_label = "NEAR_SUPPORT"
    else:
        pos_label = "MID_RANGE"

    return {
        "spot_to_resistance_pts": to_res,
        "spot_to_support_pts": to_sup,
        "range_utilization_pct": util_pct,
        "range_position": pos_label,
    }


def candidate_build_stats(candidates: list) -> dict[str, Any]:
    """Count OTM-valid iron condors in the candidate table."""
    ic = [c for c in candidates if getattr(c, "strategy", None) == "IRON_CONDOR"]
    otm_valid = 0
    for c in ic:
        sp = getattr(c, "sell_put_strike", None)
        sc = getattr(c, "sell_call_strike", None)
        if sp is None or sc is None:
            continue
        # Already filtered at build; count rows present
        otm_valid += 1
    return {
        "iron_condor_candidates": len(ic),
        "candidates_otm_valid": otm_valid,
    }


def enrich_technical(technical_dict: dict) -> dict:
    out = dict(technical_dict)
    out["bb_width_signal"] = bb_width_signal(float(technical_dict.get("bb_width", 0) or 0))
    return out


def enrich_oi(oi_dict: dict, spot: float) -> dict:
    out = dict(oi_dict)
    out.update(
        range_position_metrics(
            spot,
            int(oi_dict.get("support", 0)),
            int(oi_dict.get("resistance", 0)),
        )
    )
    return out


def enrich_greeks(
    greeks_dict: dict,
    spot: float,
    rejected_itm: int = 0,
) -> dict:
    out = dict(greeks_dict)
    spot_f = float(spot)
    move = float(greeks_dict.get("expected_daily_move", 0) or 0)
    out["expected_move_pct"] = round(move / spot_f * 100, 2) if spot_f > 0 and move > 0 else None
    candidates = greeks_dict.get("strike_candidates") or []
    stats = candidate_build_stats(candidates)
    stats["candidates_rejected_itm"] = rejected_itm
    out.update(stats)
    return out


def _meets_floors(c: dict) -> bool:
    return (c.get("est_pop_pct") or 0) >= MIN_EST_POP_PCT and (
        c.get("reward_risk") or 0
    ) >= MIN_REWARD_RISK


def classify_wait_reason(
    strategy_dict: dict,
    oi_dict: dict,
    greeks_dict: dict,
) -> tuple[str, str, str]:
    """Return (wait_primary_reason, guard_fired, guard_detail)."""
    if strategy_dict.get("strategy") != "WAIT":
        return "", "", ""

    wait = str(strategy_dict.get("wait_reason") or "")
    if strategy_dict.get("preflight_blocked"):
        return "pre_flight", "pre_flight", wait or "Pre-flight blocked"

    candidates = greeks_dict.get("strike_candidates") or []
    ic = [c for c in candidates if c.get("strategy") == "IRON_CONDOR"]
    tradeable = [c for c in ic if _meets_floors(c)]
    if ic and not tradeable:
        best_pop = max((c.get("est_pop_pct") or 0) for c in ic)
        best_rr = max((c.get("reward_risk") or 0) for c in ic)
        detail = (
            f"No IC meets floors (POP>={MIN_EST_POP_PCT}%, R:R>={MIN_REWARD_RISK}); "
            f"best POP {best_pop:.1f}%, best R:R {best_rr:.2f}"
        )
        return "pop_rr_floor", "pop_rr_floor", detail

    to_res = oi_dict.get("spot_to_resistance_pts")
    if to_res is not None and float(to_res) < 80:
        return (
            "near_resistance",
            "near_resistance",
            f"Spot {oi_dict.get('spot_to_resistance_pts')} pts below resistance "
            f"{oi_dict.get('resistance')}",
        )

    if "conflicting" in wait.lower() or strategy_dict.get("conflicting_signals"):
        return "conflicting_signals", "conflicting_signals", wait

    return "other", "other", wait or "WAIT"


def next_check_condition(oi_dict: dict, spot: float) -> str:
    """Forward-looking note for paper journal."""
    res = int(oi_dict.get("resistance", 0))
    sup = int(oi_dict.get("support", 0))
    pullback = int(sup + (res - sup) * 0.75) if res > sup else int(spot - 200)
    clear = res + 50
    pos = oi_dict.get("range_position", "")
    if pos == "NEAR_RESISTANCE":
        return (
            f"Spot must pull back below ~{pullback} (mid-range) or hold above {clear} "
            f"after clearing {res} resistance with volume."
        )
    if pos == "NEAR_SUPPORT":
        return f"Watch for hold above {sup} support; avoid new shorts if support breaks."
    return (
        f"Trade when an IC candidate clears POP>={MIN_EST_POP_PCT:.0f}% and "
        f"R:R>={MIN_REWARD_RISK} with OTM shorts."
    )


def would_trade_at_spot_hint(oi_dict: dict) -> Optional[int]:
    """Rough level where range position improves (75% from support toward resistance)."""
    sup = oi_dict.get("support")
    res = oi_dict.get("resistance")
    if sup is None or res is None:
        return None
    return int(int(sup) + (int(res) - int(sup)) * 0.75)


def enrich_strategy(
    strategy_dict: dict,
    oi_dict: dict,
    technical_dict: dict,
    greeks_dict: dict,
    spot: float,
) -> dict:
    out = dict(strategy_dict)
    out.update(range_position_metrics(spot, int(oi_dict.get("support", 0)), int(oi_dict.get("resistance", 0))))
    out["bb_width_signal"] = technical_dict.get("bb_width_signal") or bb_width_signal(
        float(technical_dict.get("bb_width", 0) or 0)
    )
    primary, guard, detail = classify_wait_reason(strategy_dict, oi_dict, greeks_dict)
    if guard:
        out["guard_fired"] = guard
        out["guard_detail"] = detail
        out["wait_primary_reason"] = primary
    out["would_trade_at_spot"] = would_trade_at_spot_hint(oi_dict)
    return out


def build_master_journal(
    agents: dict[str, Any],
    spot: float,
) -> dict[str, Any]:
    strategy = agents.get("strategy") or {}
    oi = agents.get("oi_analysis") or {}
    trade_outcome = strategy.get("strategy", "UNKNOWN")
    primary, _, _ = classify_wait_reason(strategy, oi, agents.get("greeks") or {})
    return {
        "trade_outcome": trade_outcome,
        "wait_primary_reason": primary or None,
        "paper_trade_note": "",
        "next_check_condition": next_check_condition(oi, spot),
        "spot_snapshot": round(float(spot), 2),
    }
