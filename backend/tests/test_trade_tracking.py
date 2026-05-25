"""Trade tracking: SQLite store, P&L, exit alerts."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.trade import DailyMark, TradeOpenRequest, TradeRecord
from services import trade_store
from services.position_monitor import (
    build_alert_detail,
    calculate_unrealized_pnl,
    close_trade_record,
    evaluate_exit_conditions,
)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_trades.db"
    os.environ["TRADE_DB_PATH"] = str(path)
    trade_store.init_db(path)
    yield path
    os.environ.pop("TRADE_DB_PATH", None)


def _sample_open_request(**kwargs) -> TradeOpenRequest:
    base = dict(
        trade_type="PAPER",
        run_id="run01",
        entry_spot=24000.0,
        expiry_date="02JUN26",
        dte_at_entry=8,
        strategy="IRON_CONDOR",
        sell_put_strike=23500,
        buy_put_strike=23450,
        sell_call_strike=24500,
        buy_call_strike=24550,
        entry_premium=50.0,
        max_profit=3250.0,
        max_loss=1750.0,
        lot_size=65,
        num_lots=1,
    )
    base.update(kwargs)
    return TradeOpenRequest(**base)


def test_create_and_retrieve_trade(db_path):
    tid, _ = trade_store.open_trade_from_request(_sample_open_request())
    t = trade_store.get_trade(tid, db_path)
    assert t is not None
    assert t.trade_type == "PAPER"
    assert t.status == "OPEN"
    assert t.entry_premium == 50.0


def test_paper_vs_live_flag_stored_correctly(db_path):
    tid_p, _ = trade_store.open_trade_from_request(
        _sample_open_request(trade_type="PAPER", run_id="r1")
    )
    tid_l, _ = trade_store.open_trade_from_request(
        _sample_open_request(trade_type="LIVE", run_id="r2")
    )
    assert trade_store.get_trade(tid_p).trade_type == "PAPER"
    assert trade_store.get_trade(tid_l).trade_type == "LIVE"


def test_daily_mark_calculates_pnl_correctly(db_path):
    """
    entry_premium 50, current 30 -> credit decay profit.
    (50 - 30) * 65 * 1 = 1300
    """
    tid, _ = trade_store.open_trade_from_request(_sample_open_request(run_id="pnl1"))
    trade = trade_store.get_trade(tid, db_path)
    pnl = calculate_unrealized_pnl(trade, 30.0)
    assert pnl == pytest.approx(1300.0)


def test_profit_target_alert_fires_at_50_pct(db_path):
    tid, _ = trade_store.open_trade_from_request(
        _sample_open_request(run_id="pt1", max_profit=2000.0)
    )
    trade = trade_store.get_trade(tid, db_path)
    mark = DailyMark(
        trade_id=tid,
        mark_date="2026-05-25",
        spot=24000.0,
        current_premium=32.0,
        dte_remaining=5,
        data_source="manual",
    )
    mark.unrealized_pnl = calculate_unrealized_pnl(trade, 32.0)
    assert mark.unrealized_pnl >= trade.max_profit * 0.50
    assert evaluate_exit_conditions(trade, mark) == "PROFIT_TARGET_HIT"


def test_stop_loss_alert_fires_at_100_pct(db_path):
    tid, _ = trade_store.open_trade_from_request(
        _sample_open_request(run_id="sl1", max_loss=1000.0)
    )
    trade = trade_store.get_trade(tid, db_path)
    mark = DailyMark(
        trade_id=tid,
        mark_date="2026-05-25",
        spot=24000.0,
        current_premium=80.0,
        unrealized_pnl=-1950.0,
        dte_remaining=5,
        data_source="manual",
    )
    mark.unrealized_pnl = calculate_unrealized_pnl(trade, 80.0)
    assert mark.unrealized_pnl <= -trade.max_loss
    assert evaluate_exit_conditions(trade, mark) == "STOP_LOSS_HIT"


def test_day4_alert_fires_when_dte_1(db_path):
    tid, _ = trade_store.open_trade_from_request(_sample_open_request(run_id="d4"))
    trade = trade_store.get_trade(tid, db_path)
    mark = DailyMark(
        trade_id=tid,
        mark_date="2026-05-25",
        spot=24000.0,
        current_premium=45.0,
        unrealized_pnl=325.0,
        dte_remaining=1,
        data_source="manual",
    )
    assert evaluate_exit_conditions(trade, mark) == "DAY4_CLOSE"
    detail = build_alert_detail(trade, "DAY4_CLOSE", mark)
    assert "gamma" in detail.lower()


def test_breach_call_alert_fires_when_spot_above_short_call(db_path):
    tid, _ = trade_store.open_trade_from_request(_sample_open_request(run_id="br"))
    trade = trade_store.get_trade(tid, db_path)
    mark = DailyMark(
        trade_id=tid,
        mark_date="2026-05-25",
        spot=24600.0,
        current_premium=40.0,
        unrealized_pnl=650.0,
        dte_remaining=5,
        data_source="manual",
    )
    assert evaluate_exit_conditions(trade, mark) == "BREACH_CALL"


def test_close_trade_calculates_win_loss_correctly(db_path):
    tid, _ = trade_store.open_trade_from_request(_sample_open_request(run_id="cls"))
    trade = trade_store.get_trade(tid, db_path)
    result = close_trade_record(
        trade,
        exit_premium=30.0,
        exit_spot=23900.0,
        exit_reason="50_pct_profit",
        exit_triggered_by="user_manual",
        db_path=db_path,
    )
    assert result["realized_pnl"] == pytest.approx(1300.0)
    assert result["win_loss"] == "WIN"
    closed = trade_store.get_trade(tid, db_path)
    assert closed.status == "CLOSED"
    assert closed.realized_pnl == pytest.approx(1300.0)


def test_stats_expectancy_calculation(db_path):
    for i, pnl in enumerate([1000.0, 800.0, -500.0, -400.0]):
        tid, _ = trade_store.open_trade_from_request(
            _sample_open_request(run_id=f"st{i}")
        )
        trade_store.update_trade_status(
            tid,
            "CLOSED",
            db_path,
            realized_pnl=pnl,
            win_loss="WIN" if pnl > 0 else "LOSS",
        )

    stats = trade_store.get_trade_stats(db_path=db_path)
    assert stats.closed_trades == 4
    assert stats.wins == 2
    assert stats.losses == 2
    assert stats.win_rate_pct == 50.0
    assert stats.expectancy is not None


def test_duplicate_mark_same_date_rejected(db_path):
    tid, _ = trade_store.open_trade_from_request(_sample_open_request(run_id="dup"))
    mark = DailyMark(
        trade_id=tid,
        mark_date="2026-05-25",
        data_source="manual",
    )
    trade_store.add_daily_mark(mark, db_path)
    with pytest.raises(ValueError, match="already exists"):
        trade_store.add_daily_mark(mark, db_path)


def test_reject_wait_strategy(db_path):
    with pytest.raises(ValueError, match="WAIT"):
        trade_store.open_trade_from_request(
            _sample_open_request(strategy="WAIT", run_id="w")
        )


def test_duplicate_run_id_returns_warning(db_path):
    tid1, w1 = trade_store.open_trade_from_request(_sample_open_request(run_id="duprun"))
    assert w1 is None
    tid2, w2 = trade_store.open_trade_from_request(_sample_open_request(run_id="duprun"))
    assert tid2 == tid1
    assert w2 is not None


def test_get_mark_for_date(db_path):
    tid, _ = trade_store.open_trade_from_request(_sample_open_request(run_id="mdf"))
    trade_store.add_daily_mark(
        DailyMark(trade_id=tid, mark_date="2026-05-26", data_source="manual"),
        db_path,
    )
    m = trade_store.get_mark_for_date(tid, "2026-05-26", db_path)
    assert m is not None
    assert m.mark_date == "2026-05-26"
