"""Correlated evidence engine for MIDDAY / EOD position reviews."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from config import STRIKE_INTERVAL
from models.review import PositionReview
from models.trade import TradeRecord
from services import review_store

STRATEGIES_CALL_SIDE = frozenset(
    {"IRON_CONDOR", "IRON_BUTTERFLY", "BEAR_CALL_SPREAD", "SHORT_STRANGLE"}
)
STRATEGIES_PUT_SIDE = frozenset(
    {"IRON_CONDOR", "IRON_BUTTERFLY", "BULL_PUT_SPREAD", "SHORT_STRANGLE"}
)


@dataclass
class SideMetrics:
    short_delta: float = 0.0
    delta_watch: bool = False
    delta_danger: bool = False
    delta_safe: bool = True
    oi_classification: str = "OI_STABLE"
    oi_confidence: float = 0.0


@dataclass
class CorrelatedScenarioResult:
    action_category: str = "HOLD"
    action_confidence: str = "LOW"
    evidence_list: List[str] = field(default_factory=list)
    pending_greeks: bool = False


def strategy_evaluates_call(strategy: str) -> bool:
    return strategy in STRATEGIES_CALL_SIDE


def strategy_evaluates_put(strategy: str) -> bool:
    return strategy in STRATEGIES_PUT_SIDE


def spot_at_last_review(trade_id: str, entry_spot: float, db_path=None) -> float:
    prior = review_store.get_latest_completed_review(trade_id, db_path)
    if prior and prior.current_spot is not None:
        return float(prior.current_spot)
    return float(entry_spot)


def classify_move(move_vs_expected: float) -> str:
    if move_vs_expected < 0.35:
        return "NOISE"
    if move_vs_expected < 0.75:
        return "MEANINGFUL"
    if move_vs_expected < 1.25:
        return "SIGNIFICANT"
    return "EXTREME"


def compute_move_metrics(
    trade: TradeRecord,
    spot_today: float,
    dte: int,
    db_path=None,
) -> Tuple[float, float, str]:
    spot_last = spot_at_last_review(trade.trade_id, trade.entry_spot, db_path)
    atr = float(trade.atr_at_entry or 0)
    straddle = float(trade.straddle_price_at_entry or 0)
    dte_safe = max(int(dte), 1)
    straddle_move = straddle / math.sqrt(dte_safe) if straddle > 0 else 0.0
    daily_expected = max(atr, straddle_move, 1.0)
    move_vs = abs(float(spot_today) - spot_last) / daily_expected
    return round(move_vs, 4), round(daily_expected, 2), classify_move(move_vs)


def apply_move_fields(
    review: PositionReview,
    move_vs: float,
    daily_expected: float,
    move_class: str,
) -> None:
    review.move_vs_expected = move_vs
    review.daily_expected_move = daily_expected
    review.move_class = move_class


def _parse_strikes_json(raw: Optional[str]) -> List[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _oi_at_strike(strikes: List[dict], strike: int) -> Optional[float]:
    for row in strikes:
        if int(row.get("strike", -1)) == strike:
            return float(row.get("oi", 0))
    return None


def _peak_strike(strikes: List[dict]) -> Optional[int]:
    if not strikes:
        return None
    best = max(strikes, key=lambda r: float(r.get("oi") or 0))
    strike = int(best.get("strike", 0))
    return strike if strike else None


def _oi_change_pct(entry: Optional[float], today: Optional[float]) -> Optional[float]:
    if entry is None or today is None or entry <= 0:
        return None
    return round((today - entry) / entry * 100, 1)


def classify_oi_side(
    trade: TradeRecord,
    side: str,
    top_today: List[dict],
    oi_change_pct: Optional[float],
) -> Tuple[str, float]:
    if side == "call":
        short_strike = trade.sell_call_strike
        entry_strikes = _parse_strikes_json(trade.top_call_strikes_at_entry)
        peak_entry = trade.peak_call_oi_strike_at_entry
        adj_strike = (short_strike + STRIKE_INTERVAL) if short_strike else None
    else:
        short_strike = trade.sell_put_strike
        entry_strikes = _parse_strikes_json(trade.top_put_strikes_at_entry)
        peak_entry = trade.peak_put_oi_strike_at_entry
        adj_strike = (short_strike - STRIKE_INTERVAL) if short_strike else None

    if not short_strike:
        return "OI_STABLE", 0.0

    short_entry_oi = _oi_at_strike(entry_strikes, short_strike)
    if short_entry_oi is None:
        short_entry_oi = (
            trade.sell_call_oi_at_entry if side == "call" else trade.sell_put_oi_at_entry
        )

    short_today_oi = _oi_at_strike(top_today, short_strike)
    if short_today_oi is None and oi_change_pct is not None and short_entry_oi:
        short_today_oi = short_entry_oi * (1 + oi_change_pct / 100.0)

    chg = _oi_change_pct(short_entry_oi, short_today_oi)
    if chg is None:
        return "OI_STABLE", 0.0

    adj_entry = _oi_at_strike(entry_strikes, adj_strike) if adj_strike else None
    adj_today = _oi_at_strike(top_today, adj_strike) if adj_strike else None
    adj_chg = _oi_change_pct(adj_entry, adj_today)

    peak_today = _peak_strike(top_today)
    peak_migrated = False
    if peak_entry and peak_today and peak_entry != peak_today:
        if side == "call" and peak_today > peak_entry:
            peak_migrated = True
        elif side == "put" and peak_today < peak_entry:
            peak_migrated = True

    short_up = chg > 30
    adj_down = adj_chg is not None and adj_chg < -20

    if short_up and adj_down and peak_migrated:
        return "OI_LIKELY_ROLLED", 0.6
    if short_up and (adj_down or peak_migrated):
        return "OI_POSSIBLY_ROLLED", 0.4
    if short_up:
        return "OI_LIKELY_FRESH", 0.8
    return "OI_STABLE", 0.0


def delta_thresholds(gamma_risk: str) -> Tuple[float, float]:
    if gamma_risk == "CRITICAL":
        return 0.18, 0.25
    if gamma_risk == "ELEVATED":
        return 0.22, 0.30
    return 0.25, 0.35


def _side_delta_state(abs_delta: float, gamma_risk: str) -> Tuple[bool, bool, bool]:
    watch_t, danger_t = delta_thresholds(gamma_risk)
    danger = abs_delta >= danger_t
    watch = abs_delta >= watch_t
    safe = abs_delta < watch_t
    return watch, danger, safe


def compute_theta_delta_ratio(
    trade: TradeRecord,
    review: PositionReview,
    daily_expected_move: float,
) -> Tuple[Optional[float], Optional[bool]]:
    nt = review.net_theta_today
    nd = review.net_delta_today
    if nt is None or nd is None:
        return None, None
    daily_theta_income = abs(float(nt)) * trade.lot_size * trade.num_lots
    daily_delta_risk = (
        abs(float(nd)) * daily_expected_move * trade.lot_size * trade.num_lots
    )
    if daily_delta_risk <= 0:
        return None, None
    ratio = round(daily_theta_income / daily_delta_risk, 4)
    return ratio, ratio >= 0.8


def append_supporting_evidence(
    review: PositionReview,
    trade: TradeRecord,
    evidence: List[str],
) -> None:
    if review.max_pain_shift_pts is not None and abs(review.max_pain_shift_pts) > 200:
        evidence.append(
            f"MAX_PAIN_SHIFT {review.max_pain_shift_pts:+.0f}pts "
            f"(entry {trade.max_pain_at_entry} today {review.max_pain_today})"
        )
    pcr_e, pcr_t = trade.pcr_at_entry, review.pcr_today
    if pcr_e is not None and pcr_t is not None and abs(pcr_t - pcr_e) > 0.20:
        evidence.append(f"PCR_SHIFT entry {pcr_e:.2f} today {pcr_t:.2f}")


def _close_side_action(strategy: str) -> str:
    if strategy == "BULL_PUT_SPREAD":
        return "CLOSE_PUT_SIDE"
    if strategy == "BEAR_CALL_SPREAD":
        return "CLOSE_CALL_SIDE"
    return "CLOSE_CALL_SIDE"


def _primary_side_metrics(
    trade: TradeRecord,
    review: PositionReview,
    batch: dict[str, Any],
    gamma_risk: str,
) -> SideMetrics:
    strategy = trade.strategy
    call_active = strategy_evaluates_call(strategy)
    put_active = strategy_evaluates_put(strategy)

    top_call = batch.get("top_call_strikes") or []
    top_put = batch.get("top_put_strikes") or []

    call_oi_cls, call_conf = "OI_STABLE", 0.0
    put_oi_cls, put_conf = "OI_STABLE", 0.0
    if call_active:
        call_oi_cls, call_conf = classify_oi_side(
            trade, "call", top_call, review.call_oi_change_pct
        )
        review.oi_call_classification = call_oi_cls
        review.oi_call_confidence = call_conf
    if put_active:
        put_oi_cls, put_conf = classify_oi_side(
            trade, "put", top_put, review.put_oi_change_pct
        )
        review.oi_put_classification = put_oi_cls
        review.oi_put_confidence = put_conf

    sc = abs(review.short_call_delta_today or 0.0)
    sp = abs(review.short_put_delta_today or 0.0)

    if call_active and not put_active:
        oi_cls, oi_conf = call_oi_cls, call_conf
        watch, danger, safe = _side_delta_state(sc, gamma_risk)
        delta_val = sc
    elif put_active and not call_active:
        oi_cls, oi_conf = put_oi_cls, put_conf
        watch, danger, safe = _side_delta_state(sp, gamma_risk)
        delta_val = sp
    else:
        if sc >= sp:
            oi_cls, oi_conf = call_oi_cls, call_conf
            watch, danger, safe = _side_delta_state(sc, gamma_risk)
            delta_val = sc
        else:
            oi_cls, oi_conf = put_oi_cls, put_conf
            watch, danger, safe = _side_delta_state(sp, gamma_risk)
            delta_val = sp

    return SideMetrics(
        short_delta=delta_val,
        delta_watch=watch,
        delta_danger=danger,
        delta_safe=safe,
        oi_classification=oi_cls,
        oi_confidence=oi_conf,
    )


def _theta_available(review: PositionReview) -> bool:
    return review.net_theta_today is not None and review.net_delta_today is not None


def evaluate_correlated_scenarios(
    trade: TradeRecord,
    review: PositionReview,
    side: SideMetrics,
    move_class: str,
    theta_ratio: Optional[float],
    theta_compensating: Optional[bool],
    profit_pct: Optional[float],
    dte: Optional[int],
    degraded: bool,
) -> CorrelatedScenarioResult:
    evidence: List[str] = []
    pending_greeks = False

    append_supporting_evidence(review, trade, evidence)

    if move_class == "NOISE":
        evidence.append(f"MOVE_NOISE move_vs_expected={review.move_vs_expected}")
        return CorrelatedScenarioResult("HOLD", "LOW", evidence)

    oi = side.oi_classification
    danger = side.delta_danger
    watch = side.delta_watch
    safe = side.delta_safe
    close_side = _close_side_action(trade.strategy)

    def theta_low() -> Optional[bool]:
        nonlocal pending_greeks
        if theta_ratio is None:
            if not degraded:
                pending_greeks = True
            return None
        return theta_ratio < 0.8

    def theta_high() -> Optional[bool]:
        nonlocal pending_greeks
        if theta_ratio is None:
            if not degraded:
                pending_greeks = True
            return None
        return theta_ratio >= 0.8

    # 2
    if danger and oi == "OI_LIKELY_FRESH":
        tl = theta_low()
        if tl is True:
            action = "CLOSE_FULL" if (dte is not None and dte <= 2) else close_side
            evidence.extend(
                ["delta_danger", "OI_LIKELY_FRESH", f"theta_delta_ratio={theta_ratio}"]
            )
            return CorrelatedScenarioResult(action, "HIGH", evidence, pending_greeks)
        if tl is None and not degraded:
            pending_greeks = True

    # 3
    if danger and oi == "OI_LIKELY_ROLLED":
        th = theta_high()
        if th is True or degraded:
            evidence.extend(["delta_danger", "OI_LIKELY_ROLLED", "theta_compensating"])
            return CorrelatedScenarioResult(
                "WATCH_CLOSELY", "MEDIUM", evidence, pending_greeks
            )
        if th is None and not degraded:
            pending_greeks = True

    # 4
    if danger and oi == "OI_STABLE":
        tl = theta_low()
        if tl is True or (degraded and tl is None):
            action = "CLOSE_FULL" if (degraded and dte is not None and dte <= 2) else close_side
            if degraded:
                evidence.extend(["delta_danger", "OI_STABLE", "degraded_spot_oi"])
            else:
                evidence.extend(
                    ["delta_danger", "OI_STABLE", f"theta_delta_ratio={theta_ratio}"]
                )
            return CorrelatedScenarioResult(action, "MEDIUM", evidence, pending_greeks)

    # 5
    if danger and oi == "OI_STABLE":
        th = theta_high()
        if th is True or degraded:
            evidence.extend(["delta_danger", "OI_STABLE", "theta_compensating"])
            return CorrelatedScenarioResult(
                "WATCH_CLOSELY", "MEDIUM", evidence, pending_greeks
            )

    # 6
    if oi == "OI_LIKELY_FRESH" and safe:
        evidence.extend(["OI_LIKELY_FRESH", "delta_safe"])
        return CorrelatedScenarioResult("WATCH_CLOSELY", "LOW", evidence, pending_greeks)

    # 7
    if profit_pct is not None and profit_pct >= 40 and (watch or oi == "OI_LIKELY_FRESH"):
        evidence.append(f"profit_pct={profit_pct}")
        return CorrelatedScenarioResult("TAKE_PROFIT", "MEDIUM", evidence, pending_greeks)

    # 8
    if watch and oi == "OI_STABLE":
        th = theta_high()
        if th is True or degraded:
            evidence.extend(["delta_watch", "theta_compensating", "OI_STABLE"])
            return CorrelatedScenarioResult("HOLD", "LOW", evidence, pending_greeks)
        if th is None and not degraded:
            pending_greeks = True

    evidence.append(f"default_HOLD move_class={move_class}")
    return CorrelatedScenarioResult("HOLD", "LOW", evidence, pending_greeks)


def map_action_to_exit(action: str) -> Tuple[str, str, List[str]]:
    if action == "CLOSE_FULL":
        return "HARD_EXIT", "CLOSE", [action]
    if action in (
        "CLOSE_CALL_SIDE",
        "CLOSE_PUT_SIDE",
        "TAKE_PROFIT",
        "TIGHTEN_STOPS",
        "WATCH_CLOSELY",
    ):
        return "SOFT_EXIT", "REVIEW", [action]
    return "HOLD", "HOLD", ["HOLD"]


def run_correlated_midday_eod(
    trade: TradeRecord,
    review: PositionReview,
    batch: dict[str, Any],
    db_path=None,
) -> CorrelatedScenarioResult:
    dte = review.dte_remaining if review.dte_remaining is not None else 0
    spot = float(review.current_spot or trade.entry_spot)
    move_vs, daily_expected, move_class = compute_move_metrics(
        trade, spot, dte, db_path
    )
    apply_move_fields(review, move_vs, daily_expected, move_class)

    gamma = review.gamma_risk_level or "LOW"
    side = _primary_side_metrics(trade, review, batch, gamma)

    degraded = not _theta_available(review)
    theta_ratio, theta_comp = compute_theta_delta_ratio(trade, review, daily_expected)
    review.theta_delta_ratio = theta_ratio
    review.theta_compensating = theta_comp

    result = evaluate_correlated_scenarios(
        trade,
        review,
        side,
        move_class,
        theta_ratio,
        theta_comp,
        review.pnl_pct_of_max_profit,
        dte,
        degraded=degraded,
    )
    review.pending_greeks = result.pending_greeks
    review.action_category = result.action_category
    review.action_confidence = result.action_confidence
    review.evidence_list = result.evidence_list
    return result
