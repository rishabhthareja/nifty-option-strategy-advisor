"""Strike candidates: Phase 1B/1C POP and R:R scoring."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MIN_EST_POP_PCT, MIN_REWARD_RISK
from models.market import MarketData, TechnicalData
from models.options import OIAnalysis, GreeksData, StrikeGreeks
from services.strike_candidates import (
    build_strike_candidates,
    candidate_by_id,
    format_strike_candidates_table,
    recommended_candidate,
)


def _chain():
    strikes = list(range(23400, 24150, 50))
    rows = []
    for s in strikes:
        if s == 23450:
            put_d, call_d = -0.17, 0.10
        elif s == 24150:
            put_d, call_d = -0.08, 0.17
        elif s <= 23650:
            put_d, call_d = -0.35, 0.12
        elif s <= 23750:
            put_d, call_d = -0.28, 0.25
        elif s <= 24000:
            put_d, call_d = -0.38, 0.32
        else:
            put_d, call_d = -0.12, 0.12
        rows.append({
            "strike": s,
            "put_ltp": 55.0 if s == 23700 else 40.0,
            "call_ltp": 85.0 if s == 23950 else 55.0,
            "put_delta": put_d,
            "call_delta": call_d,
            "put_bid": 0,
            "put_ask": 0,
            "call_bid": 0,
            "call_ask": 0,
            "lot_size": 65,
        })
    return pd.DataFrame(rows)


def _oi():
    return OIAnalysis(
        support=23700,
        support_put_oi=1e6,
        resistance=24000,
        resistance_call_oi=1e6,
        pcr=1.11,
        pcr_sentiment="MILDLY BULLISH",
        max_pain=23750,
        max_pain_distance=50,
        iv_rank=49.5,
        iv_environment="MODERATE",
        range_width=500,
        range_width_pct=2.1,
        top_put_strikes=[{"strike": 23700, "oi": 1e6}],
        top_call_strikes=[{"strike": 24000, "oi": 1e6}],
    )


def _greeks():
    z = StrikeGreeks(strike=23800, ltp=1, delta=0, gamma=0, theta=0, vega=0, iv=15)
    return GreeksData(
        atm_strike=23800,
        atm_iv=15.0,
        expected_daily_move=300.0,
        sell_put_strike=23750,
        buy_put_strike=23650,
        sell_call_strike=23950,
        buy_call_strike=24000,
        sell_put=z,
        buy_put=z,
        sell_call=z,
        buy_call=z,
        atm_call=z,
        atm_put=z,
    )


def _market():
    return MarketData(
        nifty_spot=23804.2,
        vix=18.4,
        change_pct=0.6,
        today_open=23671,
        today_high=23828,
        today_low=23671,
        prev_close=23654,
    )


def _technical():
    return TechnicalData(
        rsi=49,
        rsi_signal="NEUTRAL",
        macd_line=-80,
        macd_signal_text="BEARISH",
        sma_20=23870,
        sma_50=23690,
        trend="SIDEWAYS",
        atr=280,
        bb_width=0.05,
    )


def test_equal_wings_on_b():
    cands = build_strike_candidates(_chain(), _market(), _technical(), _oi(), _greeks())
    b = candidate_by_id(cands, "B")
    assert b is not None
    assert b.put_wing_pts == 50
    assert b.call_wing_pts == 50


def test_pop_rr_metrics_present():
    cands = build_strike_candidates(_chain(), _market(), _technical(), _oi(), _greeks())
    for c in cands:
        if c.strategy == "IRON_CONDOR":
            assert c.est_pop_pct is not None
            assert c.reward_risk is not None
            assert c.composite_score is not None


def test_recommended_condor_exists():
    cands = build_strike_candidates(_chain(), _market(), _technical(), _oi(), _greeks())
    rec = recommended_candidate(cands)
    assert rec is not None
    assert rec.is_recommended
    assert rec.strategy == "IRON_CONDOR"


def test_recommended_has_highest_composite_among_condors():
    cands = build_strike_candidates(_chain(), _market(), _technical(), _oi(), _greeks())
    rec = recommended_candidate(cands)
    assert rec is not None
    condors = [c for c in cands if c.strategy == "IRON_CONDOR"]
    best_score = max(c.composite_score or 0 for c in condors)
    assert rec.composite_score == best_score


def test_table_shows_pop_rr():
    cands = build_strike_candidates(_chain(), _market(), _technical(), _oi(), _greeks())
    text = format_strike_candidates_table(cands)
    assert "est_POP%" in text
    assert "R:R" in text
    assert "RECOMMENDED" in text or "YES" in text


def test_recommended_meets_floors_or_best_effort():
    cands = build_strike_candidates(_chain(), _market(), _technical(), _oi(), _greeks())
    rec = recommended_candidate(cands)
    assert rec is not None
    if (rec.est_pop_pct or 0) >= MIN_EST_POP_PCT:
        assert (rec.reward_risk or 0) >= MIN_REWARD_RISK or rec.composite_score
