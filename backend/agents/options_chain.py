import pandas as pd
from config import LOT_SIZE
from models.market import MarketData
from services.openalgo_client import OpenAlgoService
from services.expiry_utils import (
    nifty_weekly_expiry_for_session,
    parse_compact_expiry,
    resolve_chain_expiry,
    resolve_weekly_entry_expiry,
)


def options_chain_agent(market_data: MarketData) -> pd.DataFrame:
    service = OpenAlgoService()
    session_calendar = nifty_weekly_expiry_for_session()
    entry_target = resolve_weekly_entry_expiry()
    spot = float(market_data.nifty_spot)
    expiry = resolve_chain_expiry(service)
    chain = service.get_options_chain(expiry=expiry, spot=spot)
    preserved_attrs = dict(getattr(chain, "attrs", {}) or {})
    resolved = preserved_attrs.get("expiry") or expiry
    if resolved and not preserved_attrs.get("expiry"):
        preserved_attrs["expiry"] = resolved
    preserved_attrs["session_calendar_expiry"] = session_calendar
    preserved_attrs["weekly_entry_target"] = entry_target
    cal = parse_compact_expiry(session_calendar)
    res = parse_compact_expiry(resolved)
    preserved_attrs["weekly_entry_rolled"] = bool(cal and res and cal != res)

    required_cols = [
        "strike", "lot_size", "put_oi", "call_oi", "put_ltp", "call_ltp",
        "put_bid", "put_ask", "call_bid", "call_ask",
        "put_price_used", "call_price_used", "put_price_source", "call_price_source",
        "put_greeks_source", "call_greeks_source",
        "put_iv", "call_iv", "put_delta", "call_delta",
        "put_theta", "call_theta", "put_vega", "call_vega",
        "put_gamma", "call_gamma",
    ]
    for col in required_cols:
        if col not in chain.columns:
            if col == "lot_size":
                chain[col] = LOT_SIZE
            else:
                chain[col] = "" if col.endswith("_source") else 0.0

    chain = chain[required_cols].copy()
    if "lot_size" in chain.columns:
        chain.loc[chain["lot_size"].fillna(0) <= 0, "lot_size"] = LOT_SIZE
    chain["strike"] = chain["strike"].astype(int)
    out = chain.sort_values("strike").reset_index(drop=True)
    out.attrs.update(preserved_attrs)
    return out
