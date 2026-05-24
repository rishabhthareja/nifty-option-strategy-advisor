"""POP estimation: delta heuristic vs log-normal (review example)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.strike_candidates import (
    _estimate_pop_delta_heuristic,
    _estimate_pop_log_normal,
    _estimate_pop_pct,
)


def test_pop_review_example_delta_vs_log_normal():
    """
    Review example (spot 24000, shorts 23500/24500, |delta| ~0.18, IV 14%, DTE 5).

    The delta heuristic treats each short leg as independent OTM survival (~82% each)
    and yields ~67%. Log-normal uses risk-neutral d2 between breakevens (~84% here).

    The ~17pp gap is expected: independence + centering tweak understates
    probability mass inside a symmetric iron-condor band vs a single distribution.
    """
    spot = 24000.0
    lower_be = 23450.0  # short put 23500 minus ~50 net credit
    upper_be = 24550.0  # short call 24500 plus ~50 net credit
    atm_iv = 14.0
    dte = 5
    put_delta = -0.18
    call_delta = 0.18

    heuristic = _estimate_pop_delta_heuristic(
        spot, lower_be, upper_be, put_delta, call_delta, "IRON_CONDOR"
    )
    log_normal = _estimate_pop_log_normal(spot, lower_be, upper_be, atm_iv, dte)
    pop, method = _estimate_pop_pct(
        spot,
        lower_be,
        upper_be,
        put_delta,
        call_delta,
        "IRON_CONDOR",
        atm_iv=atm_iv,
        dte=dte,
    )

    assert 64.0 <= heuristic <= 70.0, f"heuristic POP expected ~67%, got {heuristic}"
    assert 81.0 <= log_normal <= 87.0, f"log-normal POP expected ~84%, got {log_normal}"
    assert pop == log_normal
    assert method == "log_normal"


def test_pop_falls_back_to_delta_when_iv_missing():
  pop, method = _estimate_pop_pct(
      24000.0,
      23450.0,
      24550.0,
      -0.18,
      0.18,
      "IRON_CONDOR",
      atm_iv=None,
      dte=5,
  )
  assert method == "delta_heuristic"
  assert 64.0 <= pop <= 70.0
