"""Phase 1 entry guards: pre-flight blockers, VIX/IV post-LLM, evaluator SSE skip."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.chain_utils import lot_size_from_chain, order_quantity
from services.entry_guards import (
    apply_iv_rank_guard,
    apply_margin_guard,
    apply_net_delta_guard,
    apply_spread_width_guard,
    apply_sr_buffer_guard,
    apply_theta_guard,
    apply_vix_guard,
    collect_hard_blockers,
    evaluator_skipped_sse_payload,
    preflight_wait,
    session_entry_blockers,
    should_skip_evaluator,
)
from models.market import MarketData
from models.options import OIAnalysis
from models.strategy import StrategyRecommendation

IST = timezone(timedelta(hours=5, minutes=30))


def _dt(year, month, day, hour=10, minute=30):
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def _market(vix=14.0, live=True):
    return MarketData(
        nifty_spot=24000.0,
        vix=vix,
        change_pct=0.1,
        today_open=23950.0,
        today_high=24050.0,
        today_low=23900.0,
        prev_close=23980.0,
        is_mock=not live,
        data_source="openalgo" if live else "mock",
    )


def _oi(iv_rank=45.0):
    return OIAnalysis(
        support=23500,
        support_put_oi=1e6,
        resistance=24500,
        resistance_call_oi=1e6,
        pcr=1.0,
        pcr_sentiment="neutral",
        max_pain=24000,
        max_pain_distance=0.0,
        iv_rank=iv_rank,
        iv_environment="MODERATE",
        range_width=1000.0,
        range_width_pct=4.0,
        top_put_strikes=[],
        top_call_strikes=[],
    )


def _chain(expiry="27MAY25", live=True, dte_ok=True):
    # Use a Tuesday-ish future expiry for DTE tests via patching or far expiry
    df = pd.DataFrame(
        [{"strike": 24000, "put_oi": 1000, "call_oi": 1000, "lot_size": 65}]
    )
    df.attrs = {
        "expiry": expiry,
        "source": "openalgo" if live else "mock",
        "chain_exchange": "NFO",
    }
    return df


class TestSessionBlockers:
    def test_weekend_blocked(self):
        sat = _dt(2025, 5, 24, 11, 0)  # Saturday
        assert session_entry_blockers(sat) == ["Market closed (weekend)."]

    def test_before_open_blocked(self):
        early = _dt(2025, 5, 23, 9, 0)  # Friday
        reasons = session_entry_blockers(early)
        assert len(reasons) == 1
        assert "Before entry window" in reasons[0]

    def test_after_close_blocked(self):
        late = _dt(2025, 5, 23, 15, 30)
        reasons = session_entry_blockers(late)
        assert len(reasons) == 1
        assert "After entry window" in reasons[0]

    def test_mid_session_clear(self):
        ok = _dt(2025, 5, 23, 11, 0)
        assert session_entry_blockers(ok) == []


class TestCollectHardBlockers:
    def test_low_vix_and_iv_rank(self):
        market = _market(vix=10.0, live=True)
        oi = _oi(iv_rank=20.0)
        chain = _chain(live=True)
        reasons = collect_hard_blockers(market, oi, chain)
        assert any("VIX" in r for r in reasons)
        assert any("IV Rank" in r for r in reasons)

    def test_mock_data_not_trade_ready(self):
        market = _market(live=False)
        oi = _oi()
        chain = _chain(live=False)
        reasons = collect_hard_blockers(market, oi, chain)
        assert any("Live quote" in r for r in reasons)


class TestPreflightWait:
    def test_returns_wait_with_preflight_flag(self):
        market = _market(vix=10.0, live=True)
        oi = _oi(iv_rank=50.0)
        chain = _chain(live=True)
        result = preflight_wait(market, oi, chain)
        assert result is not None
        assert result.strategy == "WAIT"
        assert result.preflight_blocked is True
        assert result.wait_reason.startswith("[Pre-flight]")
        assert should_skip_evaluator(result) is True

    def test_clear_market_returns_none(self, monkeypatch):
        market = _market(vix=15.0, live=True)
        oi = _oi(iv_rank=50.0)
        chain = _chain(live=True)
        monkeypatch.setattr(
            "services.entry_guards.session_entry_blockers",
            lambda now=None: [],
        )
        monkeypatch.setattr(
            "services.entry_guards.is_below_min_entry_dte",
            lambda expiry, now=None: False,
        )
        monkeypatch.setattr(
            "services.entry_guards.is_past_0dte_entry_cutoff",
            lambda expiry, now=None: False,
        )
        assert preflight_wait(market, oi, chain) is None


class TestPostLlmGuards:
    def _trade_result(self):
        return StrategyRecommendation(
            strategy="IRON_CONDOR",
            confidence="HIGH",
            reasoning="test",
            sell_put_strike=23500,
            sell_call_strike=24500,
        )

    def test_vix_guard_forces_wait(self):
        result = self._trade_result()
        apply_vix_guard(result, _market(vix=10.0))
        assert result.strategy == "WAIT"
        assert result.integrity_blocked is True

    def test_iv_rank_guard_forces_wait(self):
        result = self._trade_result()
        apply_iv_rank_guard(result, _oi(iv_rank=25.0))
        assert result.strategy == "WAIT"
        assert result.integrity_blocked is True

    def test_guards_skip_when_already_wait(self):
        result = StrategyRecommendation(
            strategy="WAIT",
            confidence="LOW",
            reasoning="already wait",
        )
        apply_vix_guard(result, _market(vix=10.0))
        apply_iv_rank_guard(result, _oi(iv_rank=10.0))
        assert result.strategy == "WAIT"
        assert result.integrity_blocked is not True


class TestThetaGuard:
    def _ic_chain(self, put_theta=-10.0, call_theta=-10.0):
        return pd.DataFrame(
            [
                {"strike": 23450, "put_theta": put_theta * 0.4, "call_theta": 0},
                {"strike": 23500, "put_theta": put_theta, "call_theta": 0},
                {"strike": 24500, "put_theta": 0, "call_theta": call_theta},
                {"strike": 24550, "put_theta": 0, "call_theta": call_theta * 0.4},
            ]
        )

    def _ic_result(self):
        return StrategyRecommendation(
            strategy="IRON_CONDOR",
            confidence="HIGH",
            reasoning="test",
            sell_put_strike=23500,
            buy_put_strike=23450,
            sell_call_strike=24500,
            buy_call_strike=24550,
            max_loss=-5000,
        )

    def test_adequate_theta_no_wait(self):
        result = self._ic_result()
        apply_theta_guard(result, self._ic_chain(), lot_multiplier=65.0)
        assert result.strategy == "IRON_CONDOR"
        assert (result.theta_per_day or 0) >= 40.0

    def test_critically_low_theta_forces_wait(self):
        result = self._ic_result()
        apply_theta_guard(result, self._ic_chain(put_theta=-0.05, call_theta=-0.05), lot_multiplier=65.0)
        assert result.strategy == "WAIT"
        assert result.integrity_blocked is True


class TestMarginGuard:
    def _trade_result(self):
        return StrategyRecommendation(
            strategy="IRON_CONDOR",
            confidence="HIGH",
            reasoning="test",
            max_loss=-10000,
            conservative_max_loss=-10000,
        )

    def test_mock_funds_skips_wait(self):
        result = self._trade_result()
        apply_margin_guard(result, {"available_margin": 0, "is_mock": True})
        assert result.strategy == "IRON_CONDOR"
        assert "mock" in (result.margin_note or "").lower()

    def test_insufficient_live_margin_forces_wait(self):
        result = self._trade_result()
        apply_margin_guard(result, {"available_margin": 5000, "is_mock": False})
        assert result.strategy == "WAIT"
        assert result.estimated_margin == 18000.0
        assert result.available_margin == 5000.0


class TestNetDeltaGuard:
    def _chain_balanced(self):
        return pd.DataFrame(
            [
                {"strike": 23450, "put_delta": -0.05, "call_delta": 0},
                {"strike": 23500, "put_delta": -0.18, "call_delta": 0},
                {"strike": 24500, "put_delta": 0, "call_delta": 0.18},
                {"strike": 24550, "put_delta": 0, "call_delta": 0.05},
            ]
        )

    def _chain_biased(self):
        return pd.DataFrame(
            [
                {"strike": 23450, "put_delta": -0.05, "call_delta": 0},
                {"strike": 23500, "put_delta": -0.30, "call_delta": 0},
                {"strike": 24500, "put_delta": 0, "call_delta": 0.10},
                {"strike": 24550, "put_delta": 0, "call_delta": 0.05},
            ]
        )

    def _ic(self):
        return StrategyRecommendation(
            strategy="IRON_CONDOR",
            confidence="HIGH",
            reasoning="test",
            sell_put_strike=23500,
            buy_put_strike=23450,
            sell_call_strike=24500,
            buy_call_strike=24550,
        )

    def test_balanced_delta_ok(self):
        result = self._ic()
        apply_net_delta_guard(result, self._chain_balanced())
        assert result.strategy == "IRON_CONDOR"
        assert result.net_delta is not None
        assert abs(result.net_delta) <= 0.10

    def test_biased_delta_downgrades(self):
        result = self._ic()
        apply_net_delta_guard(result, self._chain_biased())
        assert result.confidence == "MEDIUM"
        assert any("Net delta" in s for s in (result.conflicting_signals or []))


class TestSrBufferGuard:
    def test_short_at_support_warns(self):
        oi = _oi()
        oi.support = 23500
        oi.resistance = 24500
        result = StrategyRecommendation(
            strategy="IRON_CONDOR",
            confidence="HIGH",
            reasoning="test",
            sell_put_strike=23500,
            sell_call_strike=24400,
        )
        apply_sr_buffer_guard(result, oi)
        assert result.confidence == "MEDIUM"
        assert any("Short put" in s for s in result.conflicting_signals)

    def test_shorts_inside_range_ok(self):
        oi = _oi()
        oi.support = 23500
        oi.resistance = 24500
        result = StrategyRecommendation(
            strategy="IRON_CONDOR",
            confidence="HIGH",
            reasoning="test",
            sell_put_strike=23600,
            sell_call_strike=24400,
        )
        apply_sr_buffer_guard(result, oi)
        assert result.confidence == "HIGH"
        assert not result.conflicting_signals


class TestSpreadWidthGuard:
    def test_wide_spread_warns(self):
        chain = pd.DataFrame(
            [
                {
                    "strike": 23500,
                    "put_bid": 100,
                    "put_ask": 115,
                    "call_bid": 0,
                    "call_ask": 0,
                },
            ]
        )
        result = StrategyRecommendation(
            strategy="IRON_CONDOR",
            confidence="HIGH",
            reasoning="test",
            sell_put_strike=23500,
        )
        apply_spread_width_guard(result, chain)
        assert result.confidence == "MEDIUM"
        assert any("Wide bid-ask" in s for s in result.conflicting_signals)


class TestChainUtils:
    def test_order_quantity_uses_chain_lot(self):
        chain = pd.DataFrame([{"strike": 24000, "lot_size": 75}])
        from config import NUM_LOTS

        assert lot_size_from_chain(chain) == 75
        assert order_quantity(chain) == 75 * NUM_LOTS

    def test_lot_size_zero_falls_back_to_config(self):
        from config import LOT_SIZE

        chain = pd.DataFrame([{"strike": 24000, "lot_size": 0}])
        assert lot_size_from_chain(chain) == LOT_SIZE


class TestEvaluatorSkippedSse:
    def test_sse_payload_shape(self):
        payload = evaluator_skipped_sse_payload("pre_flight")
        assert payload == {
            "agent": "evaluator",
            "status": "skipped",
            "reason": "pre_flight",
        }

    def test_default_reason_is_pre_flight(self):
        payload = evaluator_skipped_sse_payload()
        assert payload["status"] == "skipped"
        assert payload["reason"] == "pre_flight"

    def test_sse_serializes_for_stream(self):
        import json

        payload = evaluator_skipped_sse_payload("pre_flight")
        line = f"data: {json.dumps(payload)}\n\n"
        parsed = json.loads(line[len("data: ") :].strip())
        assert parsed["agent"] == "evaluator"
        assert parsed["status"] == "skipped"
        assert parsed["reason"] == "pre_flight"
