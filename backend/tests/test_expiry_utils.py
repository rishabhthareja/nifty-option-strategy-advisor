"""Phase 1A: weekly entry DTE and chain expiry resolution."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.expiry_utils import (
    compact_expiry,
    days_to_expiry,
    expiry_context_block,
    is_below_min_entry_dte,
    nifty_weekly_expiry_for_session,
    resolve_weekly_entry_expiry,
    resolve_chain_expiry,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _dt(year, month, day, hour=10, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=IST)


class _MockChainService:
    def __init__(self, available: dict[str, str]):
        self.available = available
        self.calls: list[str] = []

    def get_options_chain(self, expiry=None):
        self.calls.append(expiry)
        key = expiry
        if key not in self.available:
            return pd.DataFrame()
        exp = self.available[key]
        df = pd.DataFrame([{"strike": 23500, "put_oi": 1, "call_oi": 1}])
        df.attrs = {"expiry": exp, "source": "mock"}
        return df

    def _next_available_expiry(self):
        return None


def test_tuesday_morning_rolls_to_next_weekly():
    # 2026-05-05 is Tuesday (Nifty weekly expiry day)
    now = _dt(2026, 5, 5)
    session = nifty_weekly_expiry_for_session(now)
    assert session == "05MAY26"
    assert days_to_expiry(session, now) == 0
    entry = resolve_weekly_entry_expiry(now)
    assert entry == "12MAY26"
    assert days_to_expiry(entry, now) == 7


def test_monday_rolls_when_one_dte():
    now = _dt(2026, 5, 4)  # Monday before Tue 05MAY26 expiry
    session = nifty_weekly_expiry_for_session(now)
    assert session == "05MAY26"
    assert days_to_expiry(session, now) == 1
    assert resolve_weekly_entry_expiry(now) == "12MAY26"


def test_friday_keeps_four_dte_weekly():
    now = _dt(2026, 5, 1)  # Friday; next Tue is 05MAY26 (4 DTE)
    session = nifty_weekly_expiry_for_session(now)
    assert session == "05MAY26"
    assert days_to_expiry(session, now) == 4
    assert resolve_weekly_entry_expiry(now) == session


def test_wednesday_keeps_six_dte():
    now = _dt(2026, 5, 6)  # Wednesday; next Tue is 12MAY26 (6 DTE)
    session = nifty_weekly_expiry_for_session(now)
    assert session == "12MAY26"
    assert days_to_expiry(session, now) == 6
    assert resolve_weekly_entry_expiry(now) == session


def test_is_below_min_entry_dte():
    now = _dt(2026, 5, 4)
    assert is_below_min_entry_dte("05MAY26", now) is True
    assert is_below_min_entry_dte("12MAY26", now) is False


def test_resolve_chain_expiry_prefers_min_dte_candidate():
    now = _dt(2026, 5, 5)
    svc = _MockChainService(
        {
            "05MAY26": "05MAY26",
            "12MAY26": "12MAY26",
        }
    )
    resolved = resolve_chain_expiry(svc, now)
    assert resolved == "12MAY26"
    assert "12MAY26" in svc.calls


def test_resolve_chain_expiry_falls_back_when_only_low_dte():
    now = _dt(2026, 5, 5)
    svc = _MockChainService({"05MAY26": "05MAY26"})
    resolved = resolve_chain_expiry(svc, now)
    assert resolved == "05MAY26"


def test_expiry_context_block_notes_roll():
    now = _dt(2026, 5, 5)
    text = expiry_context_block("12MAY26", now)
    assert "Weekly entry policy" in text
    assert "Session calendar expiry: 05MAY26" in text
    assert "rolled" in text.lower()


def test_weekly_expiry_context_block_always_has_content():
    from services.market_metrics import weekly_expiry_context_block

    svc = _MockChainService({"12MAY26": "12MAY26"})
    block = weekly_expiry_context_block(svc, 23600.0, "12MAY26", "05MAY26")
    assert "WEEKLY ENTRY EXPIRY" in block
    assert "05MAY26" in block
    assert "12MAY26" in block
    assert "Rolled forward" in block
