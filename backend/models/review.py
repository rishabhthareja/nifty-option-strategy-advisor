"""Position review models (scheduled checks + Sensibull snapshot)."""

from __future__ import annotations

import uuid
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from services.expiry_utils import ist_now

CheckSlot = Literal["MORNING", "MIDDAY", "EOD"]
ReviewStatus = Literal["COMPLETED", "PENDING_INPUT", "SKIPPED"]
ExitSignal = Literal["HARD_EXIT", "SOFT_EXIT", "HOLD"]
RecommendedAction = Literal["CLOSE", "REVIEW", "HOLD"]


class SnapshotLeg(BaseModel):
    strike: int
    type: Literal["CE", "PE"]
    action: Literal["BUY", "SELL"]
    ltp: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    delta: Optional[float] = None
    theta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None
    pnl: Optional[float] = None


class SnapshotSummary(BaseModel):
    net_premium_to_close: float
    total_pnl: Optional[float] = None
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None


class SensibullSnapshot(BaseModel):
    snapshot_time: str
    source: str = "sensibull"
    underlying: str = "NIFTY"
    spot: float
    legs: List[SnapshotLeg]
    summary: SnapshotSummary


class PositionReview(BaseModel):
    review_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    trade_id: str
    review_time: str = Field(default_factory=lambda: ist_now().isoformat())
    review_date: str = ""
    check_slot: CheckSlot
    review_status: ReviewStatus = "COMPLETED"

    exit_signal: Optional[ExitSignal] = None
    exit_reason_codes: List[str] = Field(default_factory=list)
    recommended_action: Optional[RecommendedAction] = None
    reasoning: Optional[str] = None
    reasoning_skipped: bool = False
    alert_detail: Optional[str] = None

    current_spot: Optional[float] = None
    current_premium: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    pnl_pct_of_max_profit: Optional[float] = None
    dte_remaining: Optional[int] = None
    data_source: Optional[str] = None

    iv_rank_today: Optional[float] = None
    vix_today: Optional[float] = None
    rsi_today: Optional[float] = None
    bb_width_today: Optional[float] = None
    pcr_today: Optional[float] = None
    range_position_today: Optional[str] = None

    cushion_call_entry: Optional[float] = None
    cushion_call_today: Optional[float] = None
    cushion_put_entry: Optional[float] = None
    cushion_put_today: Optional[float] = None

    call_oi_at_entry: Optional[float] = None
    call_oi_today: Optional[float] = None
    call_oi_change_pct: Optional[float] = None
    call_oi_signal: Optional[str] = None
    put_oi_at_entry: Optional[float] = None
    put_oi_today: Optional[float] = None
    put_oi_change_pct: Optional[float] = None
    put_oi_signal: Optional[str] = None
    max_pain_at_entry: Optional[float] = None
    max_pain_today: Optional[float] = None
    max_pain_shift_pts: Optional[float] = None
    max_pain_signal: Optional[str] = None

    short_call_delta_entry: Optional[float] = None
    short_call_delta_today: Optional[float] = None
    short_put_delta_entry: Optional[float] = None
    short_put_delta_today: Optional[float] = None
    net_delta_today: Optional[float] = None
    net_theta_entry: Optional[float] = None
    net_theta_today: Optional[float] = None
    net_vega_today: Optional[float] = None
    gamma_risk_level: Optional[str] = None
    delta_signal: Optional[str] = None
    theta_signal: Optional[str] = None
    vega_signal: Optional[str] = None

    snapshot_raw: Optional[str] = None
    created_at: str = Field(default_factory=lambda: ist_now().isoformat())


class PendingReviewCompleteRequest(BaseModel):
    sensibull_snapshot: Optional[SensibullSnapshot] = None
    current_premium: Optional[float] = None
    user_note: Optional[str] = None


class ReviewNowRequest(BaseModel):
    check_slot: CheckSlot
    trade_id: Optional[str] = None
