"""OpenAlgo chain: mock fallback uses caller spot, not stale default."""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.openalgo_client import OpenAlgoService


def test_mock_fallback_centres_on_spot_hint(monkeypatch):
    svc = OpenAlgoService()
    svc._connected = False
    spot = 23952.4
    chain = svc.get_options_chain(expiry="02JUN26", spot=spot)
    assert chain.attrs.get("source") == "mock"
    strikes = chain["strike"].astype(int).tolist()
    assert min(strikes) >= 23600
    assert max(strikes) <= 24300
    atm = round(spot / 50) * 50
    assert atm in strikes
