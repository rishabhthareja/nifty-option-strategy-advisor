"""Correlated position review engine tests."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.trade import TradeOpenRequest
from services import trade_store
from services.correlated_review import (
    classify_move,
    classify_oi_side,
    compute_move_metrics,
    compute_theta_delta_ratio,
    evaluate_correlated_scenarios,
    map_action_to_exit,
    strategy_evaluates_call,
    strategy_evaluates_put,
)
from models.review import PositionReview
from models.trade import TradeRecord


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "corr.db"
    os.environ["TRADE_DB_PATH"] = str(path)
    trade_store.init_db(path)
    yield path
    os.environ.pop("TRADE_DB_PATH", None)


def _trade(**kwargs) -> TradeRecord:
    base = dict(
        trade_id="t1",
        trade_type="PAPER",
        status="OPEN",
        entry_date="2026-05-26",
        entry_spot=24000.0,
        expiry_date="26MAY26",
        dte_at_entry=0,
        strategy="IRON_CONDOR",
        sell_put_strike=23550,
        buy_put_strike=23450,
        sell_call_strike=23950,
        buy_call_strike=24050,
        entry_premium=64.0,
        max_profit=4000.0,
        max_loss=2000.0,
        lot_size=65,
        num_lots=1,
        atr_at_entry=200.0,
        straddle_price_at_entry=300.0,
        sell_call_oi_at_entry=100000.0,
        sell_put_oi_at_entry=100000.0,
        top_call_strikes_at_entry=json.dumps(
            [
                {"strike": 23950, "oi": 100000},
                {"strike": 24000, "oi": 150000},
            ]
        ),
        top_put_strikes_at_entry=json.dumps(
            [{"strike": 23550, "oi": 100000}]
        ),
        peak_call_oi_strike_at_entry=24000,
        peak_put_oi_strike_at_entry=23550,
    )
    base.update(kwargs)
    return TradeRecord(**base)


def test_classify_move_noise():
    assert classify_move(0.2) == "NOISE"
    assert classify_move(0.34) == "NOISE"
    assert classify_move(0.5) == "MEANINGFUL"


def test_strategy_side_flags():
    assert strategy_evaluates_call("IRON_CONDOR")
    assert strategy_evaluates_put("IRON_CONDOR")
    assert strategy_evaluates_put("BULL_PUT_SPREAD")
    assert not strategy_evaluates_call("BULL_PUT_SPREAD")
    assert strategy_evaluates_call("BEAR_CALL_SPREAD")
    assert not strategy_evaluates_put("BEAR_CALL_SPREAD")


def test_compute_move_metrics_uses_entry_spot_when_no_prior_review(db_path):
    t = _trade()
    trade_store.create_trade(t, db_path)
    move_vs, daily, cls = compute_move_metrics(t, 24010.0, 0, db_path)
    assert daily >= 200.0
    assert cls == "NOISE"
    assert move_vs < 0.35


def test_oi_likely_fresh():
    t = _trade()
    top_today = [
        {"strike": 23950, "oi": 180000},
        {"strike": 24000, "oi": 160000},
    ]
    cls, conf = classify_oi_side(t, "call", top_today, None)
    assert cls == "OI_LIKELY_FRESH"
    assert conf == 0.8


def test_oi_stable_when_flat():
    t = _trade()
    top_today = [{"strike": 23950, "oi": 105000}]
    cls, conf = classify_oi_side(t, "call", top_today, 5.0)
    assert cls == "OI_STABLE"
    assert conf == 0.0


def test_theta_ratio_none_when_greeks_missing():
    t = _trade()
    rev = PositionReview(
        trade_id="t1",
        check_slot="MIDDAY",
        review_date="2026-05-26",
        net_theta_today=None,
        net_delta_today=None,
    )
    ratio, comp = compute_theta_delta_ratio(t, rev, 200.0)
    assert ratio is None
    assert comp is None


def test_theta_ratio_computed():
    t = _trade()
    rev = PositionReview(
        trade_id="t1",
        check_slot="MIDDAY",
        review_date="2026-05-26",
        net_theta_today=-2.0,
        net_delta_today=0.05,
    )
    ratio, comp = compute_theta_delta_ratio(t, rev, 200.0)
    assert ratio is not None
    assert comp in (True, False)


def test_noise_holds_regardless():
    t = _trade()
    rev = PositionReview(
        trade_id="t1",
        check_slot="MIDDAY",
        review_date="2026-05-26",
        move_class="NOISE",
        move_vs_expected=0.1,
    )
    side = type("S", (), {
        "oi_classification": "OI_LIKELY_FRESH",
        "delta_danger": True,
        "delta_watch": True,
        "delta_safe": False,
        "short_delta": 0.3,
        "oi_confidence": 0.8,
    })()
    result = evaluate_correlated_scenarios(
        t, rev, side, "NOISE", 0.5, True, 50.0, 0, degraded=False
    )
    assert result.action_category == "HOLD"


def test_map_close_full_is_hard():
    sig, rec, codes = map_action_to_exit("CLOSE_FULL")
    assert sig == "HARD_EXIT"
    assert rec == "CLOSE"
    assert codes == ["CLOSE_FULL"]


def test_map_watch_closely_is_soft():
    sig, rec, _ = map_action_to_exit("WATCH_CLOSELY")
    assert sig == "SOFT_EXIT"
    assert rec == "REVIEW"


def test_open_trade_stores_enrichment(db_path):
    top_call = [{"strike": 23950, "oi": 1e5}]
    req = TradeOpenRequest(
        trade_type="PAPER",
        entry_spot=24000.0,
        expiry_date="26MAY26",
        dte_at_entry=3,
        strategy="IRON_CONDOR",
        sell_put_strike=23500,
        buy_put_strike=23450,
        sell_call_strike=24500,
        buy_call_strike=24550,
        entry_premium=80.0,
        max_profit=5000.0,
        max_loss=9000.0,
        lot_size=65,
        atr_at_entry=210.0,
        straddle_price_at_entry=320.0,
        top_call_strikes_at_entry=json.dumps(top_call),
        peak_call_oi_strike_at_entry=23950,
    )
    tid, _ = trade_store.open_trade_from_request(req, db_path)
    row = trade_store.get_trade(tid, db_path)
    assert row.atr_at_entry == 210.0
    assert row.straddle_price_at_entry == 320.0
    assert json.loads(row.top_call_strikes_at_entry)[0]["strike"] == 23950
