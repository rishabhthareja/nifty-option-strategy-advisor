"""Journal metrics for session logs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.journal_metrics import (
    bb_width_signal,
    classify_wait_reason,
    enrich_oi,
    range_position_metrics,
)


def test_range_position_near_resistance():
    m = range_position_metrics(23969.0, 23000, 24000)
    assert m["spot_to_resistance_pts"] == 31.0
    assert m["range_position"] == "NEAR_RESISTANCE"
    assert m["range_utilization_pct"] == 96.9


def test_bb_width_signal_bands():
    assert bb_width_signal(0.03) == "TIGHT"
    assert bb_width_signal(0.0479) == "MODERATE"
    assert bb_width_signal(0.08) == "WIDE"


def test_classify_wait_pop_floor():
    strategy = {"strategy": "WAIT", "wait_reason": "floors", "preflight_blocked": False}
    oi = enrich_oi({"support": 23000, "resistance": 24000}, 23969.0)
    greeks = {
        "strike_candidates": [
            {
                "strategy": "IRON_CONDOR",
                "est_pop_pct": 45.0,
                "reward_risk": 1.1,
            }
        ]
    }
    primary, guard, _ = classify_wait_reason(strategy, oi, greeks)
    assert primary == "pop_rr_floor"
    assert guard == "pop_rr_floor"
