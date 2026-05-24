"""Tests for reliability metrics."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from services.market_metrics import (
    compute_vix_percentile,
    straddle_expected_move,
    intraday_context,
)
from services.expiry_utils import is_past_0dte_entry_cutoff
from datetime import datetime, timezone, timedelta
from models.market import MarketData


class _FakeService:
    def get_vix_ohlcv(self, days=252):
        return pd.DataFrame({"close": list(range(12, 12 + 30))})


def test_vix_percentile_from_history():
    rank, method = compute_vix_percentile(_FakeService(), 18.0)
    assert method == "vix_percentile_1y"
    assert 0 <= rank <= 100


def test_straddle_expected_move():
    chain = pd.DataFrame([
        {"strike": 23500, "call_ltp": 100.0, "put_ltp": 90.0},
    ])
    move, price, method = straddle_expected_move(chain, 23500, 23500.0)
    assert method == "atm_straddle_ltp"
    assert move == 190.0
    assert price == 190.0


def test_intraday_context():
    m = MarketData(
        nifty_spot=23600,
        vix=18,
        change_pct=0.1,
        today_open=23500,
        today_high=23650,
        today_low=23450,
        prev_close=23580,
    )
    text = intraday_context(m)
    assert "today open" in text.lower()
    assert "% of range" in text


def test_0dte_cutoff():
    ist = timezone(timedelta(hours=5, minutes=30))
    expiry = "19MAY26"
    afternoon = datetime(2026, 5, 19, 15, 0, tzinfo=ist)
    assert is_past_0dte_entry_cutoff(expiry, afternoon) is True
    morning = datetime(2026, 5, 19, 11, 0, tzinfo=ist)
    assert is_past_0dte_entry_cutoff(expiry, morning) is False


if __name__ == "__main__":
    test_vix_percentile_from_history()
    test_straddle_expected_move()
    test_intraday_context()
    test_0dte_cutoff()
    print("all ok")
