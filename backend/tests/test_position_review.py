"""Position review scheduler, rules, and pending flow."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.review import PendingReviewCompleteRequest, PositionReview, SensibullSnapshot, SnapshotLeg, SnapshotSummary
from models.trade import TradeOpenRequest
from services import review_store, trade_store
from services.expiry_utils import compact_expiry, ist_now
from services.position_review import (
    build_hard_reasoning,
    complete_pending_review,
    is_trading_session,
    run_position_review,
    validate_sensibull_snapshot,
    _evaluate_hard,
    _evaluate_soft,
    _populate_review_base,
)

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "review_test.db"
    os.environ["TRADE_DB_PATH"] = str(path)
    trade_store.init_db(path)
    yield path
    os.environ.pop("TRADE_DB_PATH", None)


def _open_ic(db_path, trade_type="PAPER", run_id="r1", **extra) -> str:
    exp = compact_expiry((ist_now() + timedelta(days=5)).date())
    fields = dict(
        trade_type=trade_type,
        run_id=run_id,
        entry_spot=24000.0,
        expiry_date=exp,
        dte_at_entry=5,
        strategy="IRON_CONDOR",
        sell_put_strike=23500,
        buy_put_strike=23450,
        sell_call_strike=24500,
        buy_call_strike=24550,
        entry_premium=80.0,
        max_profit=5200.0,
        max_loss=9800.0,
        lot_size=65,
        iv_rank_at_entry=40.0,
        vix_at_entry=14.0,
        rsi_at_entry=50.0,
        bb_width_at_entry=0.04,
        pcr_at_entry=1.0,
        sell_call_oi_at_entry=100000.0,
        sell_put_oi_at_entry=120000.0,
        max_pain_at_entry=24000.0,
        sell_call_delta_at_entry=0.15,
        sell_put_delta_at_entry=-0.15,
        net_theta_at_entry=100.0,
    )
    fields.update(extra)
    req = TradeOpenRequest(**fields)
    tid, _ = trade_store.open_trade_from_request(req, db_path)
    return tid


def _mock_batch(spot=24000.0, iv_rank=35.0):
    oi = MagicMock()
    oi.iv_rank = iv_rank
    oi.pcr = 1.05
    oi.max_pain = 24000
    oi.support = 23800
    oi.resistance = 24200
    oi.top_put_strikes = [{"strike": 23500, "oi": 150000}]
    oi.top_call_strikes = [{"strike": 24500, "oi": 200000}]
    import pandas as pd

    chain = pd.DataFrame(
        {
            "strike": [23500, 24500],
            "put_oi": [150000, 0],
            "call_oi": [0, 200000],
            "put_delta": [-0.2, 0],
            "call_delta": [0, 0.2],
            "put_theta": [-4, 0],
            "call_theta": [0, -4],
            "put_vega": [10, 0],
            "call_vega": [0, 10],
            "put_ltp": [50, 0],
            "call_ltp": [0, 50],
            "put_bid": [49, 0],
            "call_bid": [0, 49],
            "put_ask": [51, 0],
            "call_ask": [0, 51],
        }
    )
    return {
        "spot": spot,
        "vix": 15.0,
        "iv_rank": iv_rank,
        "pcr": 1.05,
        "max_pain": 24000,
        "rsi": 52.0,
        "bb_width": 0.045,
        "range_position": "MID_RANGE",
        "support": 23800,
        "resistance": 24200,
        "top_put_strikes": oi.top_put_strikes,
        "top_call_strikes": oi.top_call_strikes,
        "oi": oi,
        "chain": chain,
        "market": MagicMock(),
    }


def test_run_position_review_morning_breach_fires_hard_exit(db_path):
    tid = _open_ic(db_path)
    trade = trade_store.get_trade(tid, db_path)
    batch = _mock_batch(spot=24600.0)
    review = _populate_review_base(trade, batch, "MORNING")
    review.current_premium = 90.0
    review.unrealized_pnl = -650.0
    notes = []
    hard = _evaluate_hard(trade, review, "MORNING", notes)
    assert "BREACH_CALL" in hard
    text = build_hard_reasoning(trade, review, hard)
    assert "short call" in text.lower()


@patch("services.position_review.fetch_review_market_data")
def test_run_position_review_midday_soft_exit_paper_creates_pending(mock_batch, db_path):
    mock_batch.return_value = _mock_batch(spot=24000.0, iv_rank=20.0)
    tid = _open_ic(db_path, trade_type="PAPER")
    with patch("services.position_review.fetch_current_premium", return_value={"current_premium": None}):
        result = run_position_review("MIDDAY", trade_id=tid, bypass_session=True)
    assert not result.get("skipped")
    pending = review_store.get_latest_pending_review(tid, db_path)
    assert pending is not None
    assert pending.review_status == "PENDING_INPUT"


def test_pending_review_completed_with_manual_premium(db_path):
    tid = _open_ic(db_path, trade_type="PAPER")
    review = PositionReview(
        trade_id=tid,
        review_date=ist_now().date().isoformat(),
        check_slot="MIDDAY",
        review_status="PENDING_INPUT",
        current_spot=24000.0,
        dte_remaining=5,
        iv_rank_today=30.0,
        cushion_call_today=500.0,
        cushion_put_today=500.0,
    )
    review_store.upsert_review(review, db_path)
    with patch("agents.position_review_llm.generate_soft_exit_reasoning", return_value="Consider closing."):
        out = complete_pending_review(
            tid,
            PendingReviewCompleteRequest(current_premium=40.0),
        )
    assert out.review_status == "COMPLETED"
    assert out.current_premium == 40.0
    assert out.unrealized_pnl == pytest.approx(2600.0)


def test_snapshot_leg_mismatch_returns_400(db_path):
    tid = _open_ic(db_path)
    trade = trade_store.get_trade(tid, db_path)
    snap = SensibullSnapshot(
        snapshot_time=ist_now().isoformat(),
        spot=24000.0,
        legs=[
            SnapshotLeg(strike=23500, type="PE", action="BUY", ltp=10),
        ],
        summary=SnapshotSummary(net_premium_to_close=40.0),
    )
    with pytest.raises(ValueError) as exc:
        validate_sensibull_snapshot(trade, snap)
    assert "Leg mismatch" in str(exc.value)


def test_scheduler_noop_on_weekend():
    sat = datetime(2026, 5, 23, 12, 0, tzinfo=IST)
    assert is_trading_session(sat) is False


def test_scheduler_noop_outside_session():
    early = datetime(2026, 5, 25, 8, 0, tzinfo=IST)
    assert is_trading_session(early) is False


def test_review_idempotent_same_slot_date_overwrites(db_path):
    tid = _open_ic(db_path)
    r1 = PositionReview(
        trade_id=tid,
        review_date="2026-05-25",
        check_slot="MORNING",
        review_status="COMPLETED",
        exit_signal="HOLD",
        recommended_action="HOLD",
    )
    review_store.upsert_review(r1, db_path)
    r2 = PositionReview(
        trade_id=tid,
        review_date="2026-05-25",
        check_slot="MORNING",
        review_status="COMPLETED",
        exit_signal="HARD_EXIT",
        recommended_action="CLOSE",
        exit_reason_codes=["BREACH_CALL"],
    )
    review_store.upsert_review(r2, db_path)
    rows = review_store.list_reviews_for_trade(tid, db_path)
    morning = [x for x in rows if x.check_slot == "MORNING" and x.review_date == "2026-05-25"]
    assert len(morning) == 1
    assert morning[0].exit_signal == "HARD_EXIT"


def test_eod_dte1_escalates_soft_to_hard(db_path):
    tid = _open_ic(db_path)
    trade = trade_store.get_trade(tid, db_path)
    batch = _mock_batch()
    review = _populate_review_base(trade, batch, "EOD")
    review.dte_remaining = 1
    review.current_premium = 50.0
    review.unrealized_pnl = 1000.0
    notes = []
    soft = ["PROFIT_TARGET_HIT"]
    hard = []
    from services.position_review import _apply_slot_escalation

    hard, soft = _apply_slot_escalation(hard, soft, "EOD", trade, review)
    assert "DTE_MANDATORY_CLOSE" in hard or len(soft) == 0


def test_oi_building_soft_exit_fires_above_40pct(db_path):
    tid = _open_ic(db_path)
    trade = trade_store.get_trade(tid, db_path)
    batch = _mock_batch()
    review = _populate_review_base(trade, batch, "MIDDAY")
    review.call_oi_change_pct = 50.0
    review.current_premium = 60.0
    review.unrealized_pnl = 1300.0
    codes = _evaluate_soft(trade, review, "MIDDAY", [])
    assert "CALL_OI_BUILDING" in codes


def test_theta_collapsed_soft_exit_fires_below_30pct(db_path):
    tid = _open_ic(db_path)
    trade = trade_store.get_trade(tid, db_path)
    batch = _mock_batch()
    review = _populate_review_base(trade, batch, "MIDDAY")
    review.net_theta_entry = 100.0
    review.net_theta_today = 20.0
    review.current_premium = 60.0
    review.unrealized_pnl = 100.0
    codes = _evaluate_soft(trade, review, "MIDDAY", [])
    assert "THETA_COLLAPSED" in codes


def test_review_summary_returns_entry_vs_today(db_path):
    tid = _open_ic(db_path)
    r = PositionReview(
        trade_id=tid,
        review_date=ist_now().date().isoformat(),
        check_slot="MIDDAY",
        review_status="COMPLETED",
        exit_signal="HOLD",
        recommended_action="HOLD",
        iv_rank_today=33.0,
    )
    review_store.upsert_review(r, db_path)
    from services.position_review import build_review_summary

    summary = build_review_summary(tid)
    assert summary["entry"]["trade_id"] == tid
    assert summary["latest_review"]["iv_rank_today"] == 33.0
