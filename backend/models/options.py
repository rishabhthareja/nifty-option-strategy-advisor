from pydantic import BaseModel
from typing import List, Dict, Any


class StrikeGreeks(BaseModel):
    strike: int
    ltp: float
    price_used: float = 0.0
    price_source: str = "ltp"
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float
    greeks_source: str = "calculated_ltp"


class OIAnalysis(BaseModel):
    support: int
    support_put_oi: float
    resistance: int
    resistance_call_oi: float
    pcr: float
    pcr_sentiment: str
    max_pain: int
    max_pain_distance: float
    iv_rank: float
    iv_environment: str
    iv_rank_method: str = ""
    pcr_scope: str = "loaded_chain"
    max_pain_scope: str = "loaded_chain"
    range_width: float
    range_width_pct: float
    top_put_strikes: List[Dict[str, Any]]
    top_call_strikes: List[Dict[str, Any]]


class GreeksData(BaseModel):
    atm_strike: int
    atm_iv: float
    expected_daily_move: float
    expected_move_method: str = "formula"
    straddle_price: float = 0.0
    sell_put_strike: int
    buy_put_strike: int
    sell_call_strike: int
    buy_call_strike: int
    sell_put: StrikeGreeks
    buy_put: StrikeGreeks
    sell_call: StrikeGreeks
    buy_call: StrikeGreeks
    atm_call: StrikeGreeks
    atm_put: StrikeGreeks
    greeks_source: str = "calculated_ltp"
