from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from models.compat import Literal


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


class StrikeCandidate(BaseModel):
    candidate_id: str
    strategy: Literal[
        "IRON_CONDOR",
        "IRON_BUTTERFLY",
        "BULL_PUT_SPREAD",
        "BEAR_CALL_SPREAD",
        "SHORT_STRANGLE",
    ]
    label: str
    sell_put_strike: Optional[int] = None
    buy_put_strike: Optional[int] = None
    sell_call_strike: Optional[int] = None
    buy_call_strike: Optional[int] = None
    net_premium_ltp: float = 0.0
    max_profit_ltp: float = 0.0
    max_loss_ltp: float = 0.0
    lower_breakeven: Optional[float] = None
    upper_breakeven: Optional[float] = None
    short_put_delta: Optional[float] = None
    short_call_delta: Optional[float] = None
    put_distance_pts: Optional[float] = None
    call_distance_pts: Optional[float] = None
    vs_implied_move: str = ""
    notes: str = ""
    est_pop_pct: Optional[float] = None
    pop_method: Optional[str] = None
    reward_risk: Optional[float] = None
    composite_score: Optional[float] = None
    put_wing_pts: Optional[int] = None
    call_wing_pts: Optional[int] = None
    is_recommended: bool = False


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
    strike_candidates: List[StrikeCandidate] = []
