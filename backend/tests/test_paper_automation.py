"""Paper automation, chain premium fetch, and monitoring mode filters."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.strategy import StrategyRecommendation
from models.trade import TradeOpenRequest, TradeRecord
from services import trade_store
from services.position_monitor import (
    auto_create_paper_trade,
    fetch_current_premium,
    list_open_live_trades_for_monitoring,
)
from services.scheduler import auto_refresh_paper_pnl


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test_trades.db"
    os.environ["TRADE_DB_PATH"] = str(path)
    trade_store.init_db(path)
    yield path
    os.environ.pop("TRADE_DB_PATH", None)


def _sample_trade(**kwargs) -> TradeRecord:
    base = dict(
        trade_id="t1",
        trade_type="PAPER",
        status="OPEN",
        entry_date="2026-05-26",
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
    return TradeRecord(**base)


def _chain_df(*, mock: bool = False) -> pd.DataFrame:
    rows = []
    for strike in (23450, 23500, 24500, 24550):
        rows.append({
            "strike": strike,
            "put_bid": 1.0,
            "put_ask": 1.2,
            "put_ltp": 1.1,
            "put_delta": -0.1,
            "put_theta": -2.0,
            "call_bid": 2.0,
            "call_ask": 2.2,
            "call_ltp": 2.1,
            "call_delta": 0.15,
            "call_theta": -3.0,
        })
    df = pd.DataFrame(rows)
    df.attrs["source"] = "mock" if mock else "openalgo"
    return df


def test_fetch_premium_with_chain_uses_chain_prices():
    trade = _sample_trade()
    chain = _chain_df()
    result = fetch_current_premium(trade, chain=chain)
    assert result["current_premium"] is not None
    assert result["data_source"] == "chain_ltp"
    assert result.get("current_greeks") is not None


def test_fetch_premium_mock_chain_returns_none():
    trade = _sample_trade()
    chain = _chain_df(mock=True)
    result = fetch_current_premium(trade, chain=chain)
    assert result["current_premium"] is None
    assert result.get("is_mock") is True


def test_fetch_premium_near_zero_not_rejected():
    trade = _sample_trade()
    chain = _chain_df()
    with patch(
        "services.position_monitor._price_legs_from_chain",
        return_value=0.50,
    ):
        result = fetch_current_premium(trade, chain=chain)
    assert result["current_premium"] == 0.50


def test_auto_create_paper_trade_success(db_path):
    strategy = StrategyRecommendation(
        strategy="IRON_CONDOR",
        confidence="MEDIUM",
        option_expiry="02JUN26",
        days_to_expiry=8,
        sell_put_strike=23500,
        buy_put_strike=23450,
        sell_call_strike=24500,
        buy_call_strike=24550,
        net_premium=55.0,
        max_profit=3500.0,
        max_loss=1800.0,
        reasoning="test",
    )
    market = MagicMock(nifty_spot=24000.0, vix=14.0)
    oi = MagicMock(top_call_strikes=[], top_put_strikes=[])
    greeks = MagicMock(straddle_price=None)
    chain = _chain_df()
    with patch("config.AUTO_PAPER_TRADE", True):
        result = auto_create_paper_trade(
            strategy=strategy,
            oi=oi,
            greeks=greeks,
            chain=chain,
            market=market,
            run_id="run-auto-1",
        )
    assert "trade_id" in result
    t = trade_store.get_trade(result["trade_id"], db_path)
    assert t.trade_type == "PAPER"
    assert t.sell_put_entry_ltp is not None


def test_auto_create_skips_duplicate(db_path):
    req = TradeOpenRequest(
        trade_type="PAPER",
        run_id="dup-run",
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
    )
    trade_store.open_trade_from_request(req, db_path)

    strategy = StrategyRecommendation(
        strategy="IRON_CONDOR",
        confidence="MEDIUM",
        option_expiry="02JUN26",
        sell_put_strike=23500,
        buy_put_strike=23450,
        sell_call_strike=24500,
        buy_call_strike=24550,
        net_premium=55.0,
        max_profit=3500.0,
        max_loss=1800.0,
        reasoning="test",
    )
    market = MagicMock(nifty_spot=24000.0, vix=14.0)
    oi = MagicMock(top_call_strikes=[], top_put_strikes=[])
    greeks = MagicMock(straddle_price=None)
    with patch("config.AUTO_PAPER_TRADE", True):
        result = auto_create_paper_trade(
            strategy=strategy,
            oi=oi,
            greeks=greeks,
            chain=_chain_df(),
            market=market,
            run_id="run-dup",
        )
    assert "skipped" in result
    assert "Duplicate" in result["skipped"]


def test_auto_create_skips_wait(db_path):
    strategy = StrategyRecommendation(strategy="WAIT", confidence="LOW", reasoning="wait")
    market = MagicMock(nifty_spot=24000.0, vix=14.0)
    oi = MagicMock(top_call_strikes=[], top_put_strikes=[])
    greeks = MagicMock(straddle_price=None)
    with patch("config.AUTO_PAPER_TRADE", True):
        result = auto_create_paper_trade(
            strategy=strategy,
            oi=oi,
            greeks=greeks,
            chain=_chain_df(),
            market=market,
            run_id="run-wait",
        )
    assert result == {"skipped": "Strategy is WAIT"}


def test_monitoring_mode_fires_when_live_trade_open(db_path):
    trade_store.create_trade(
        _sample_trade(trade_type="LIVE", trade_id="live1"),
        db_path,
    )
    open_live = list_open_live_trades_for_monitoring()
    assert len(open_live) == 1
    assert open_live[0].trade_id == "live1"


def test_monitoring_mode_skips_expired_trade(db_path):
    trade_store.create_trade(
        _sample_trade(
            trade_type="LIVE",
            trade_id="expired1",
            expiry_date="01JAN20",
        ),
        db_path,
    )
    open_live = list_open_live_trades_for_monitoring()
    assert open_live == []


def test_entry_mode_when_no_live_trade(db_path):
    trade_store.create_trade(
        _sample_trade(trade_type="PAPER", trade_id="paper1"),
        db_path,
    )
    open_live = list_open_live_trades_for_monitoring()
    assert open_live == []


def test_hourly_refresh_skips_mock_chain(db_path):
    tid, _ = trade_store.open_trade_from_request(
        TradeOpenRequest(
            trade_type="PAPER",
            run_id="hr1",
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
        ),
        db_path,
    )

    mock_chain = _chain_df(mock=True)
    mock_svc = MagicMock()
    mock_svc._connected = True
    mock_svc.get_live_data.return_value = {"nifty_spot": 24000}
    mock_svc.get_options_chain.return_value = mock_chain

    with patch("services.scheduler.is_trading_session", return_value=True):
        with patch("services.openalgo_client.OpenAlgoService", return_value=mock_svc):
            auto_refresh_paper_pnl()

    marks = trade_store.get_marks_for_trade(tid, db_path)
    assert marks == []
