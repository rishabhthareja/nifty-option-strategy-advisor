"""Scheduled position review: market fetch, exit rules, PAPER pending flow."""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional, Tuple

import pandas as pd

from agents.greeks import _get_strike_greeks
from agents.oi_analysis import oi_analysis_agent
from agents.options_chain import options_chain_agent
from agents.position_review_llm import generate_soft_exit_reasoning
from agents.technical import technical_agent
from models.market import MarketData
from models.review import (
    PendingReviewCompleteRequest,
    PositionReview,
    SensibullSnapshot,
    SnapshotLeg,
)
from models.trade import TradeRecord
from services import review_store, trade_store
from services.expiry_utils import days_to_expiry, is_expiry_day, ist_now
from services.journal_metrics import range_position_metrics
from services.live_market_cache import get_cached_live_data
from services.position_monitor import (
    calculate_pnl_pct_of_max_profit,
    calculate_unrealized_pnl,
    fetch_current_premium,
)
from services.openalgo_client import OpenAlgoService

logger = logging.getLogger(__name__)

HARD_CODES = {
    "BREACH_CALL",
    "BREACH_PUT",
    "MAX_LOSS_HIT",
    "DTE_MANDATORY_CLOSE",
    "EXPIRY_DAY_EOD",
    "IV_RANK_FLOOR",
    "GAMMA_CRITICAL",
    "DELTA_EXTREME_CALL",
    "DELTA_EXTREME_PUT",
    "CALL_OI_EXTREME",
}

SOFT_CODES = {
    "PROFIT_TARGET_HIT",
    "CUSHION_WARNING_CALL",
    "CUSHION_WARNING_PUT",
    "RSI_DRIFT",
    "BB_REGIME_CHANGE",
    "IV_RANK_DROP",
    "PCR_SHIFT",
    "CALL_OI_BUILDING",
    "PUT_WALL_COLLAPSING",
    "MAX_PAIN_SHIFT",
    "DELTA_EXPANSION_CALL",
    "DELTA_EXPANSION_PUT",
    "THETA_COLLAPSED",
    "GAMMA_ELEVATED",
    "PNL_DETERIORATION",
    "VIX_SPIKE",
}


def is_trading_session(now=None) -> bool:
    now = now or ist_now()
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 30


def fetch_review_market_data(
    include_technical: bool = True,
) -> dict[str, Any]:
    """One batch fetch per scheduler run — shared across all open trades."""
    live = get_cached_live_data()
    spot = float(live.get("nifty_spot") or 0)
    vix = float(live.get("vix") or 0)
    market = MarketData(
        nifty_spot=spot,
        vix=vix,
        change_pct=float(live.get("change_pct") or 0),
        today_open=float(live.get("today_open") or spot),
        today_high=float(live.get("today_high") or spot),
        today_low=float(live.get("today_low") or spot),
        prev_close=float(live.get("prev_close") or spot),
        is_mock=bool(live.get("is_mock")),
    )
    chain = options_chain_agent(market)
    oi = oi_analysis_agent(chain, market)
    tech = None
    rsi = None
    bb_width = None
    if include_technical:
        tech = technical_agent(market)
        rsi = tech.rsi
        bb_width = tech.bb_width

    rp = range_position_metrics(spot, oi.support, oi.resistance)
    return {
        "market": market,
        "chain": chain,
        "oi": oi,
        "technical": tech,
        "spot": spot,
        "vix": vix,
        "iv_rank": oi.iv_rank,
        "pcr": oi.pcr,
        "max_pain": oi.max_pain,
        "rsi": rsi,
        "bb_width": bb_width,
        "range_position": rp["range_position"],
        "support": oi.support,
        "resistance": oi.resistance,
        "top_put_strikes": oi.top_put_strikes,
        "top_call_strikes": oi.top_call_strikes,
    }


def _oi_at_strike(
    chain: pd.DataFrame,
    strike: int,
    side: str,
    top_list: list,
) -> Optional[float]:
    for row in top_list or []:
        if int(row.get("strike", -1)) == strike:
            return float(row.get("oi", 0))
    rows = chain[chain["strike"] == strike]
    if rows.empty:
        return None
    r = rows.iloc[0]
    return float(r["put_oi"] if side == "put" else r["call_oi"])


def _oi_change_pct(entry: Optional[float], today: Optional[float]) -> Optional[float]:
    if entry is None or today is None or entry <= 0:
        return None
    return round((today - entry) / entry * 100, 1)


def _oi_signal(change_pct: Optional[float], building_thresh: float = 40, unwind_thresh: float = -30) -> str:
    if change_pct is None:
        return "STABLE"
    if change_pct > building_thresh:
        return "BUILDING"
    if change_pct < unwind_thresh:
        return "UNWINDING"
    return "STABLE"


def _leg_greeks_from_chain(chain: pd.DataFrame, strike: int, option_type: str):
    return _get_strike_greeks(chain, strike, option_type)


def _greeks_from_snapshot(snapshot: SensibullSnapshot) -> dict[str, Any]:
    sc = sp = bc = bp = None
    for leg in snapshot.legs:
        if leg.type == "CE" and leg.action == "SELL":
            sc = leg
        elif leg.type == "PE" and leg.action == "SELL":
            sp = leg
        elif leg.type == "CE" and leg.action == "BUY":
            bc = leg
        elif leg.type == "PE" and leg.action == "BUY":
            bp = leg

    def _t(leg: Optional[SnapshotLeg]) -> float:
        return float(leg.theta) if leg and leg.theta is not None else 0.0

    def _v(leg: Optional[SnapshotLeg]) -> float:
        return float(leg.vega) if leg and leg.vega is not None else 0.0

    def _d(leg: Optional[SnapshotLeg], is_put: bool) -> float:
        if not leg or leg.delta is None:
            return 0.0
        return float(leg.delta)

    net_theta = -_t(sp) - _t(sc) + _t(bp) + _t(bc)
    net_vega = -_v(sp) - _v(sc) + _v(bp) + _v(bc)
    sc_delta = _d(sc, False)
    sp_delta = _d(sp, True)
    net_delta = -sc_delta - sp_delta
    return {
        "short_call_delta_today": sc_delta,
        "short_put_delta_today": sp_delta,
        "net_delta_today": round(net_delta, 4),
        "net_theta_today": round(net_theta, 4),
        "net_vega_today": round(net_vega, 4),
        "current_premium": snapshot.summary.net_premium_to_close,
    }


def _greeks_from_live_chain(trade: TradeRecord, chain: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sc_delta = sp_delta = 0.0
    net_theta = net_vega = 0.0
    legs = []
    if trade.sell_put_strike:
        legs.append(("put", trade.sell_put_strike, "short"))
    if trade.buy_put_strike:
        legs.append(("put", trade.buy_put_strike, "long"))
    if trade.sell_call_strike:
        legs.append(("call", trade.sell_call_strike, "short"))
    if trade.buy_call_strike:
        legs.append(("call", trade.buy_call_strike, "long"))

    for side, strike, role in legs:
        g = _leg_greeks_from_chain(chain, int(strike), side)
        sign = -1 if role == "short" else 1
        if role == "short" and side == "call":
            sc_delta = g.delta
        if role == "short" and side == "put":
            sp_delta = g.delta
        net_theta += sign * (-g.theta)
        net_vega += sign * (-g.vega)

    out["short_call_delta_today"] = sc_delta
    out["short_put_delta_today"] = sp_delta
    out["net_delta_today"] = round(-sc_delta - sp_delta, 4)
    out["net_theta_today"] = round(net_theta, 4)
    out["net_vega_today"] = round(net_vega, 4)
    return out


def validate_sensibull_snapshot(
    trade: TradeRecord, snapshot: SensibullSnapshot
) -> None:
    expected = []
    if trade.sell_put_strike:
        expected.append((trade.sell_put_strike, "PE", "SELL"))
    if trade.buy_put_strike:
        expected.append((trade.buy_put_strike, "PE", "BUY"))
    if trade.sell_call_strike:
        expected.append((trade.sell_call_strike, "CE", "SELL"))
    if trade.buy_call_strike:
        expected.append((trade.buy_call_strike, "CE", "BUY"))

    for strike, typ, action in expected:
        match = [
            lg
            for lg in snapshot.legs
            if lg.strike == strike and lg.type == typ
        ]
        if not match:
            raise ValueError(
                json.dumps(
                    {
                        "error": "Leg mismatch",
                        "expected": f"{strike} {typ} {action}",
                        "found": "missing",
                    }
                )
            )
        leg = match[0]
        if leg.action != action:
            raise ValueError(
                json.dumps(
                    {
                        "error": "Leg mismatch",
                        "expected": f"{strike} {typ} {action}",
                        "found": f"{strike} {typ} {leg.action}",
                    }
                )
            )


def _populate_review_base(
    trade: TradeRecord,
    batch: dict[str, Any],
    check_slot: str,
) -> PositionReview:
    now = ist_now()
    spot = batch["spot"]
    oi = batch["oi"]
    chain = batch["chain"]

    dte = days_to_expiry(trade.expiry_date, now)
    cushion_call_entry = (
        (trade.sell_call_strike - trade.entry_spot)
        if trade.sell_call_strike
        else None
    )
    cushion_put_entry = (
        (trade.entry_spot - trade.sell_put_strike)
        if trade.sell_put_strike
        else None
    )
    cushion_call_today = (
        (trade.sell_call_strike - spot) if trade.sell_call_strike else None
    )
    cushion_put_today = (
        (spot - trade.sell_put_strike) if trade.sell_put_strike else None
    )

    call_oi_today = (
        _oi_at_strike(chain, trade.sell_call_strike, "call", batch["top_call_strikes"])
        if trade.sell_call_strike
        else None
    )
    put_oi_today = (
        _oi_at_strike(chain, trade.sell_put_strike, "put", batch["top_put_strikes"])
        if trade.sell_put_strike
        else None
    )
    call_entry = trade.sell_call_oi_at_entry
    put_entry = trade.sell_put_oi_at_entry
    call_chg = _oi_change_pct(call_entry, call_oi_today)
    put_chg = _oi_change_pct(put_entry, put_oi_today)

    mp_entry = trade.max_pain_at_entry
    mp_today = float(oi.max_pain)
    mp_shift = round(mp_today - mp_entry, 1) if mp_entry is not None else None

    review = PositionReview(
        trade_id=trade.trade_id,
        review_time=now.isoformat(),
        review_date=now.date().isoformat(),
        check_slot=check_slot,
        current_spot=spot,
        dte_remaining=dte,
        iv_rank_today=batch.get("iv_rank"),
        vix_today=batch.get("vix"),
        rsi_today=batch.get("rsi"),
        bb_width_today=batch.get("bb_width"),
        pcr_today=batch.get("pcr"),
        range_position_today=batch.get("range_position"),
        cushion_call_entry=cushion_call_entry,
        cushion_call_today=cushion_call_today,
        cushion_put_entry=cushion_put_entry,
        cushion_put_today=cushion_put_today,
        call_oi_at_entry=call_entry,
        call_oi_today=call_oi_today,
        call_oi_change_pct=call_chg,
        call_oi_signal=_oi_signal(call_chg),
        put_oi_at_entry=put_entry,
        put_oi_today=put_oi_today,
        put_oi_change_pct=put_chg,
        put_oi_signal=_oi_signal(put_chg),
        max_pain_at_entry=mp_entry,
        max_pain_today=mp_today,
        max_pain_shift_pts=mp_shift,
        max_pain_signal=(
            "STABLE"
            if mp_shift is None or abs(mp_shift) <= 200
            else ("SHIFTING_BULLISH" if mp_shift > 0 else "SHIFTING_BEARISH")
        ),
        short_call_delta_entry=trade.sell_call_delta_at_entry,
        short_put_delta_entry=trade.sell_put_delta_at_entry,
        net_theta_entry=trade.net_theta_at_entry,
    )
    return review


def _gamma_risk_level(dte: Optional[int], sc_delta: float, sp_delta: float) -> str:
    if dte is None:
        return "LOW"
    ad_sc = abs(sc_delta)
    ad_sp = abs(sp_delta)
    if dte <= 0:
        return "CRITICAL"
    if dte <= 1 and (ad_sc > 0.20 or ad_sp > 0.20):
        return "CRITICAL"
    if dte <= 2 and (ad_sc > 0.25 or ad_sp > 0.25):
        return "HIGH"
    if dte <= 2 and (ad_sc > 0.25 or ad_sp > 0.25):
        return "ELEVATED"
    if ad_sc > 0.35 or ad_sp > 0.35:
        return "ELEVATED"
    return "LOW"


def _evaluate_hard(
    trade: TradeRecord,
    review: PositionReview,
    check_slot: str,
    notes: List[str],
) -> List[str]:
    codes: List[str] = []
    spot = review.current_spot
    if spot is not None:
        if trade.sell_call_strike and spot > trade.sell_call_strike:
            codes.append("BREACH_CALL")
        if trade.sell_put_strike and spot < trade.sell_put_strike:
            codes.append("BREACH_PUT")

    if review.unrealized_pnl is not None and trade.max_loss:
        if review.unrealized_pnl <= -float(trade.max_loss):
            codes.append("MAX_LOSS_HIT")

    if review.dte_remaining is not None and review.dte_remaining <= 1:
        codes.append("DTE_MANDATORY_CLOSE")

    if is_expiry_day(trade.expiry_date) and check_slot == "EOD":
        codes.append("EXPIRY_DAY_EOD")

    iv_today = review.iv_rank_today
    iv_entry = trade.iv_rank_at_entry
    if iv_entry is not None and iv_today is not None:
        if iv_today < 15 and iv_entry > 25:
            codes.append("IV_RANK_FLOOR")
    elif iv_entry is None:
        notes.append("IV_RANK_FLOOR skipped — iv_rank_at_entry missing.")

    sc = review.short_call_delta_today or 0
    sp = review.short_put_delta_today or 0
    if review.dte_remaining is not None and review.dte_remaining <= 1:
        if sc > 0.20 or abs(sp) > 0.20:
            codes.append("GAMMA_CRITICAL")
    if sc > 0.45:
        codes.append("DELTA_EXTREME_CALL")
    if abs(sp) > 0.45:
        codes.append("DELTA_EXTREME_PUT")

    if review.call_oi_change_pct is not None and review.call_oi_change_pct > 100:
        codes.append("CALL_OI_EXTREME")

    return codes


def _evaluate_soft(
    trade: TradeRecord,
    review: PositionReview,
    check_slot: str,
    notes: List[str],
) -> List[str]:
    codes: List[str] = []
    spot = review.current_spot

    if review.unrealized_pnl is not None and trade.max_profit:
        if review.unrealized_pnl >= float(trade.max_profit) * 0.50:
            codes.append("PROFIT_TARGET_HIT")

    if spot is not None:
        if trade.sell_call_strike and (trade.sell_call_strike - spot) < 100:
            codes.append("CUSHION_WARNING_CALL")
        if trade.sell_put_strike and (spot - trade.sell_put_strike) < 100:
            codes.append("CUSHION_WARNING_PUT")

    if check_slot == "MORNING" and review.vix_today and trade.vix_at_entry is not None:
        if review.vix_today > trade.vix_at_entry + 4 and review.vix_today > 20:
            codes.append("VIX_SPIKE")

    if check_slot != "MORNING":
        if review.rsi_today is not None and (review.rsi_today > 65 or review.rsi_today < 35):
            codes.append("RSI_DRIFT")
        bb_t = review.bb_width_today
        bb_e = trade.bb_width_at_entry
        if bb_t is not None and bb_e is not None and bb_t > 0.07 and bb_e < 0.05:
            codes.append("BB_REGIME_CHANGE")

    iv_today = review.iv_rank_today
    iv_entry = trade.iv_rank_at_entry
    if iv_entry is not None and iv_today is not None:
        if iv_today < (iv_entry - 15):
            codes.append("IV_RANK_DROP")

    pcr_e = trade.pcr_at_entry
    pcr_t = review.pcr_today
    if pcr_e is not None and pcr_t is not None:
        if abs(pcr_t - pcr_e) > 0.20:
            codes.append("PCR_SHIFT")

    if review.call_oi_change_pct is not None and review.call_oi_change_pct > 40:
        codes.append("CALL_OI_BUILDING")
    if review.put_oi_change_pct is not None and review.put_oi_change_pct < -30:
        codes.append("PUT_WALL_COLLAPSING")

    if review.max_pain_shift_pts is not None and abs(review.max_pain_shift_pts) > 200:
        codes.append("MAX_PAIN_SHIFT")

    sc = review.short_call_delta_today or 0
    sp = review.short_put_delta_today or 0
    if sc > 0.35:
        codes.append("DELTA_EXPANSION_CALL")
    if abs(sp) > 0.35:
        codes.append("DELTA_EXPANSION_PUT")

    nt = review.net_theta_today
    ne = review.net_theta_entry
    if nt is not None and ne is not None and ne > 0 and nt < ne * 0.30:
        codes.append("THETA_COLLAPSED")

    if review.dte_remaining is not None and review.dte_remaining <= 2:
        if sc > 0.25 or abs(sp) > 0.25:
            codes.append("GAMMA_ELEVATED")

    marks = trade_store.get_last_n_marks(trade.trade_id, 3)
    if len(marks) >= 3:
        pnls = [m.unrealized_pnl for m in reversed(marks)]
        if all(
            pnls[i] is not None
            and pnls[i + 1] is not None
            and pnls[i + 1] < pnls[i]
            for i in range(len(pnls) - 1)
        ):
            codes.append("PNL_DETERIORATION")
    else:
        notes.append(
            "PNL_DETERIORATION skipped — fewer than 3 daily marks available."
        )

    return codes


def build_hard_reasoning(trade: TradeRecord, review: PositionReview, codes: List[str]) -> str:
    spot = review.current_spot
    parts = []
    if "BREACH_CALL" in codes:
        parts.append(
            f"Spot {spot} has crossed your short call {trade.sell_call_strike}. Close immediately."
        )
    if "BREACH_PUT" in codes:
        parts.append(
            f"Spot {spot} is below your short put {trade.sell_put_strike}. Close immediately."
        )
    if "MAX_LOSS_HIT" in codes:
        parts.append(
            f"Unrealized loss ₹{review.unrealized_pnl:,.0f} reached max loss ₹{trade.max_loss:,.0f}."
        )
    if "DTE_MANDATORY_CLOSE" in codes:
        parts.append("1 day to expiry. Close today to avoid gamma explosion.")
    if "EXPIRY_DAY_EOD" in codes:
        parts.append("Expiry day 3 PM — mandatory close regardless of P&L.")
    if "IV_RANK_FLOOR" in codes:
        parts.append("IV Rank collapsed below 15% — premium edge gone.")
    if "GAMMA_CRITICAL" in codes:
        parts.append("Gamma risk critical near expiry with elevated short deltas.")
    if "DELTA_EXTREME_CALL" in codes:
        parts.append("Short call delta extreme — directional risk too high.")
    if "DELTA_EXTREME_PUT" in codes:
        parts.append("Short put delta extreme — directional risk too high.")
    if "CALL_OI_EXTREME" in codes:
        parts.append("Call OI at short strike up >100% — institutional accumulation.")
    return " ".join(parts) if parts else "Hard exit conditions met — close position."


def _apply_slot_escalation(
    hard: List[str],
    soft: List[str],
    check_slot: str,
    trade: TradeRecord,
    review: PositionReview,
) -> Tuple[List[str], List[str]]:
    if check_slot == "EOD":
        if review.dte_remaining is not None and review.dte_remaining <= 1:
            for c in list(soft):
                if c not in hard:
                    hard.append(c)
            soft = []
        midday = review_store.get_midday_review_today(trade.trade_id, review.review_date)
        if midday and midday.exit_signal == "SOFT_EXIT" and not hard:
            if "DTE_MANDATORY_CLOSE" not in hard:
                hard.append("DTE_MANDATORY_CLOSE")
    return hard, soft


def _finalize_review(
    trade: TradeRecord,
    review: PositionReview,
    hard: List[str],
    soft: List[str],
    check_slot: str,
    notes: List[str],
    use_llm: bool = True,
) -> PositionReview:
    review.alert_detail = " | ".join(notes) if notes else None

    if hard:
        review.exit_signal = "HARD_EXIT"
        review.exit_reason_codes = hard
        review.recommended_action = "CLOSE"
        review.reasoning = build_hard_reasoning(trade, review, hard)
        review.reasoning_skipped = False
        review.review_status = "COMPLETED"
        return review

    if soft:
        review.exit_signal = "SOFT_EXIT"
        review.exit_reason_codes = soft
        review.recommended_action = "REVIEW"
        review.review_status = "COMPLETED"
        if use_llm and check_slot in ("MIDDAY", "EOD"):
            if check_slot == "EOD":
                midday = review_store.get_midday_review_today(
                    trade.trade_id, review.review_date
                )
                if midday and midday.reasoning:
                    review.reasoning = (
                        f"{midday.reasoning}\n\n[EOD Update: DTE now {review.dte_remaining}. "
                        "Closing today is strongly recommended.]"
                    )
                    review.reasoning_skipped = False
                    return review
            try:
                review.reasoning = generate_soft_exit_reasoning(trade, review)
                review.reasoning_skipped = False
            except Exception as e:
                logger.exception("LLM reasoning failed: %s", e)
                review.reasoning = (
                    "Soft exit signals fired. Review position and consider closing."
                )
                review.reasoning_skipped = False
        else:
            review.reasoning = (
                "Soft exit signals detected. Review before next session."
            )
            review.reasoning_skipped = True
        return review

    review.exit_signal = "HOLD"
    review.exit_reason_codes = []
    review.recommended_action = "HOLD"
    if check_slot == "MORNING":
        review.reasoning = "Position healthy at open — no hard exit triggers."
    else:
        review.reasoning = "Conditions stable — hold and let theta work."
    review.reasoning_skipped = True
    review.review_status = "COMPLETED"
    return review


def _review_one_trade(
    trade: TradeRecord,
    batch: dict[str, Any],
    check_slot: str,
) -> PositionReview:
    now = ist_now()
    review_date = now.date().isoformat()
    review_store.mark_pending_as_skipped(trade.trade_id, check_slot, review_date)

    include_technical = check_slot != "MORNING"
    if not include_technical:
        batch = {**batch, "rsi": None, "bb_width": None}

    review = _populate_review_base(trade, batch, check_slot)
    notes: List[str] = []

    premium = None
    data_source = None
    greeks_extra: dict[str, Any] = {}

    if trade.trade_type == "LIVE":
        fr = fetch_current_premium(trade)
        if fr.get("current_premium"):
            premium = fr["current_premium"]
            data_source = "broker"
            chain = batch["chain"]
            greeks_extra = _greeks_from_live_chain(trade, chain)
    else:
        data_source = "pending"

    review.data_source = data_source
    if premium is not None:
        review.current_premium = premium
        review.unrealized_pnl = calculate_unrealized_pnl(trade, premium)
        review.pnl_pct_of_max_profit = calculate_pnl_pct_of_max_profit(
            trade, review.unrealized_pnl
        )
        for k, v in greeks_extra.items():
            if v is not None:
                setattr(review, k, v)

    sc = review.short_call_delta_today or 0
    sp = review.short_put_delta_today or 0
    review.gamma_risk_level = _gamma_risk_level(review.dte_remaining, sc, sp)

    hard = _evaluate_hard(trade, review, check_slot, notes)
    if hard:
        review = _finalize_review(trade, review, hard, [], check_slot, notes, use_llm=False)
        review_store.upsert_review(review)
        return review

    soft_spot_only = _evaluate_soft(trade, review, check_slot, notes)
    needs_premium_soft = any(
        c in soft_spot_only
        for c in ("PROFIT_TARGET_HIT", "THETA_COLLAPSED", "PNL_DETERIORATION")
    )

    if trade.trade_type == "PAPER" and premium is None:
        review.review_status = "PENDING_INPUT"
        review.exit_signal = None
        review.recommended_action = None
        review.exit_reason_codes = list(soft_spot_only)
        review.alert_detail = " | ".join(notes) if notes else None
        review.reasoning = (
            "Paper trade — submit Sensibull snapshot or current premium to complete review."
        )
        review.reasoning_skipped = True
        review_store.upsert_review(review)
        return review

    soft = _evaluate_soft(trade, review, check_slot, notes) if premium else soft_spot_only
    hard, soft = _apply_slot_escalation(hard, soft, check_slot, trade, review)
    if hard:
        review = _finalize_review(trade, review, hard, [], check_slot, notes, use_llm=False)
        review_store.upsert_review(review)
        return review

    use_llm = check_slot in ("MIDDAY", "EOD")
    if check_slot == "MORNING":
        use_llm = False

    review = _finalize_review(
        trade, review, [], soft, check_slot, notes, use_llm=use_llm
    )
    review_store.upsert_review(review)
    return review


def run_position_review(
    check_slot: str,
    trade_id: Optional[str] = None,
    bypass_session: bool = False,
) -> dict[str, Any]:
    if not bypass_session and not is_trading_session():
        return {"skipped": True, "reason": "outside_trading_session"}

    results = {"check_slot": check_slot, "reviews": [], "errors": []}
    try:
        include_technical = check_slot != "MORNING"
        batch = fetch_review_market_data(include_technical=include_technical)
    except Exception as e:
        logger.exception("Market batch fetch failed: %s", e)
        results["errors"].append(str(e))
        return results

    trades = trade_store.list_trades(status="OPEN")
    if trade_id:
        trades = [t for t in trades if t.trade_id == trade_id]

    for trade in trades:
        try:
            review = _review_one_trade(trade, batch, check_slot)
            results["reviews"].append(
                {
                    "trade_id": trade.trade_id,
                    "review_id": review.review_id,
                    "status": review.review_status,
                    "exit_signal": review.exit_signal,
                    "recommended_action": review.recommended_action,
                }
            )
        except Exception as e:
            logger.exception("Review failed for %s: %s", trade.trade_id, e)
            results["errors"].append(f"{trade.trade_id}: {e}")

    return results


def complete_pending_review(
    trade_id: str,
    body: PendingReviewCompleteRequest,
) -> PositionReview:
    trade = trade_store.get_trade(trade_id)
    if not trade:
        raise ValueError("Trade not found")
    pending = review_store.get_latest_pending_review(trade_id)
    if not pending:
        raise ValueError("No pending review for this trade")

    premium = body.current_premium
    greeks_extra: dict[str, Any] = {}
    snapshot_raw = None

    if body.sensibull_snapshot:
        validate_sensibull_snapshot(trade, body.sensibull_snapshot)
        greeks_extra = _greeks_from_snapshot(body.sensibull_snapshot)
        premium = greeks_extra.pop("current_premium")
        snapshot_raw = body.sensibull_snapshot.model_dump_json()
        pending.current_spot = body.sensibull_snapshot.spot
    elif premium is None:
        raise ValueError("Provide sensibull_snapshot or current_premium")

    pending.current_premium = premium
    pending.data_source = "manual"
    pending.unrealized_pnl = calculate_unrealized_pnl(trade, premium)
    pending.pnl_pct_of_max_profit = calculate_pnl_pct_of_max_profit(
        trade, pending.unrealized_pnl
    )
    pending.snapshot_raw = snapshot_raw
    for k, v in greeks_extra.items():
        setattr(pending, k, v)

    notes: List[str] = []
    hard = _evaluate_hard(trade, pending, pending.check_slot, notes)
    if hard:
        review = _finalize_review(
            trade, pending, hard, [], pending.check_slot, notes, use_llm=False
        )
    else:
        soft = _evaluate_soft(trade, pending, pending.check_slot, notes)
        hard, soft = _apply_slot_escalation(
            hard, soft, pending.check_slot, trade, pending
        )
        use_llm = pending.check_slot in ("MIDDAY", "EOD")
        review = _finalize_review(
            trade, pending, hard, soft, pending.check_slot, notes, use_llm=use_llm
        )

    review_store.upsert_review(review)
    return review


def build_review_summary(trade_id: str) -> dict[str, Any]:
    trade = trade_store.get_trade(trade_id)
    if not trade:
        raise ValueError("Trade not found")
    latest = review_store.get_latest_completed_review(trade_id)
    return {
        "entry": trade.model_dump(),
        "latest_review": latest.model_dump() if latest else None,
    }
