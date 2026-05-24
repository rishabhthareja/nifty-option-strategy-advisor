"""Background-refreshed NIFTY/VIX cache — /live-data never blocks on OpenAlgo."""

import threading
import time
from typing import Optional

from config import LIVE_DATA_CACHE_SECS
from services.openalgo_client import OpenAlgoService

_lock = threading.Lock()
_svc: Optional[OpenAlgoService] = None
_cache: Optional[dict] = None
_cache_ts: float = 0.0
_refresh_thread: Optional[threading.Thread] = None
_started = False


def _get_svc() -> OpenAlgoService:
    global _svc
    if _svc is None:
        _svc = OpenAlgoService()
    return _svc


def _default_snapshot() -> dict:
    svc = _get_svc()
    spot = svc._mock_spot
    prev_close = spot - 45.25
    return {
        "nifty_spot": spot,
        "vix": svc._mock_vix,
        "change_pct": round((spot - prev_close) / prev_close * 100, 2) if prev_close else 0.0,
        "today_open": 22301.20,
        "today_high": 22415.75,
        "today_low": 22280.50,
        "prev_close": prev_close,
        "is_mock": True,
        "data_source": "warming",
    }


def _refresh_once() -> None:
    global _cache, _cache_ts
    try:
        data = _get_svc().get_market_data()
    except Exception as e:
        print(f"[live_market_cache] refresh failed: {e}")
        return

    with _lock:
        if not data.get("is_mock"):
            _cache = {k: v for k, v in data.items() if k not in ("cached", "cache_age_sec", "stale", "warming")}
            _cache_ts = time.time()


def _refresh_loop() -> None:
    while True:
        _refresh_once()
        time.sleep(LIVE_DATA_CACHE_SECS)


def start_background_refresh() -> None:
    global _refresh_thread, _started
    if _started:
        return
    _started = True
    _refresh_once()
    _refresh_thread = threading.Thread(target=_refresh_loop, daemon=True, name="live-market-refresh")
    _refresh_thread.start()


def get_cached_live_data() -> dict:
    """Instant read — never calls OpenAlgo on the request thread."""
    now = time.time()
    with _lock:
        if _cache is not None:
            age = round(now - _cache_ts, 1)
            stale = age > LIVE_DATA_CACHE_SECS * 2
            out = {**_cache, "cached": True, "cache_age_sec": age}
            if stale:
                out["stale"] = True
            return out

    snap = _default_snapshot()
    snap["warming"] = True
    return snap
