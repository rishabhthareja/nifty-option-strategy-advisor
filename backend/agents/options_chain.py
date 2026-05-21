import pandas as pd
from models.market import MarketData
from services.openalgo_client import OpenAlgoService
from services.expiry_utils import resolve_chain_expiry


def options_chain_agent(market_data: MarketData) -> pd.DataFrame:
    service = OpenAlgoService()
    expiry = resolve_chain_expiry(service)
    chain = service.get_options_chain(expiry=expiry)
    preserved_attrs = dict(getattr(chain, "attrs", {}) or {})
    if expiry and not preserved_attrs.get("expiry"):
        preserved_attrs["expiry"] = expiry

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
            chain[col] = "" if col.endswith("_source") else 0.0

    chain = chain[required_cols].copy()
    chain["strike"] = chain["strike"].astype(int)
    out = chain.sort_values("strike").reset_index(drop=True)
    out.attrs.update(preserved_attrs)
    return out
