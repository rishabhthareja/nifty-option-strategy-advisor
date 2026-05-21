from pydantic import BaseModel
from typing import List, Optional, Literal


class StrategyRecommendation(BaseModel):
    strategy: Literal[
        "IRON_CONDOR",
        "IRON_BUTTERFLY",
        "BULL_PUT_SPREAD",
        "BEAR_CALL_SPREAD",
        "SHORT_STRANGLE",
        "WAIT",
    ]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    sell_put_strike: Optional[int] = None
    buy_put_strike: Optional[int] = None
    sell_call_strike: Optional[int] = None
    buy_call_strike: Optional[int] = None
    sell_put_premium: Optional[float] = None
    buy_put_premium: Optional[float] = None
    sell_call_premium: Optional[float] = None
    buy_call_premium: Optional[float] = None
    net_premium: Optional[float] = None
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None
    lower_breakeven: Optional[float] = None
    upper_breakeven: Optional[float] = None
    # Paper-trade / diagnostics (filled in Python, not by LLM)
    snapshot_time_ist: Optional[str] = None
    session_phase: Optional[str] = None
    quotes_live: Optional[bool] = None
    chain_live: Optional[bool] = None
    data_trade_ready: Optional[bool] = None
    integrity_blocked: Optional[bool] = None
    integrity_note: Optional[str] = None
    option_expiry: Optional[str] = None
    days_to_expiry: Optional[int] = None
    is_expiry_day: Optional[bool] = None
    structure_note: Optional[str] = None
    conservative_net_premium: Optional[float] = None
    conservative_max_profit: Optional[float] = None
    conservative_max_loss: Optional[float] = None
    conservative_lower_breakeven: Optional[float] = None
    conservative_upper_breakeven: Optional[float] = None
    aligned_signals: List[str] = []
    conflicting_signals: List[str] = []
    reasoning: str
    wait_reason: Optional[str] = None


class EvaluationResult(BaseModel):
    quality_score: float
    recommendation_confidence: Literal["LOW", "MEDIUM", "HIGH"]
    evidence_supporting: List[str] = []
    evidence_contradicting: List[str] = []
    gaps_in_analysis: List[str] = []
    is_validated: bool
    evaluator_reasoning: str
    suggested_adjustment: Optional[str] = None


class OrderLeg(BaseModel):
    symbol: str
    action: str
    quantity: int
    premium: float
    leg: str


class OrderDetails(BaseModel):
    strategy: str
    expiry: str
    legs: List[OrderLeg]
    net_premium_actual: float
    max_profit_actual: float
    max_loss_actual: float
    lower_breakeven: Optional[float] = None
    upper_breakeven: Optional[float] = None
    quality_score: float


class ExecutionResult(BaseModel):
    status: str
    timestamp: str
    orders: List[dict]
    human_approved: bool
