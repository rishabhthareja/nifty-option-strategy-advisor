"""Populate trade-open enrichment fields from analysis agent outputs."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _peak_oi_strike(strikes: Optional[List[Dict[str, Any]]]) -> Optional[int]:
    if not strikes:
        return None
    best = max(strikes, key=lambda r: float(r.get("oi") or 0))
    strike = int(best.get("strike", 0))
    return strike if strike else None


def entry_enrichment_from_agents(
    technical: Any = None,
    oi: Any = None,
    greeks: Any = None,
) -> Dict[str, Any]:
    """
    Build optional TradeOpenRequest fields from technical / oi / greeks outputs.
    Safe when agents are dicts or Pydantic models.
    """

    def _get(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    top_call = _get(oi, "top_call_strikes") or []
    top_put = _get(oi, "top_put_strikes") or []

    out: Dict[str, Any] = {}
    atr = _get(technical, "atr")
    straddle = _get(greeks, "straddle_price")
    if atr is not None:
        out["atr_at_entry"] = round(float(atr), 2)
    if straddle is not None:
        out["straddle_price_at_entry"] = round(float(straddle), 2)
    if top_call:
        out["top_call_strikes_at_entry"] = json.dumps(top_call)
        peak = _peak_oi_strike(top_call if isinstance(top_call, list) else list(top_call))
        if peak:
            out["peak_call_oi_strike_at_entry"] = peak
    if top_put:
        out["top_put_strikes_at_entry"] = json.dumps(top_put)
        peak = _peak_oi_strike(top_put if isinstance(top_put, list) else list(top_put))
        if peak:
            out["peak_put_oi_strike_at_entry"] = peak
    return out
