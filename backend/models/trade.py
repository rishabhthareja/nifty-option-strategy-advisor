"""Trade lifecycle models (paper + live tracking)."""

from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

from services.expiry_utils import ist_now


class TradeRecord(BaseModel):
    trade_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    trade_type: Literal["PAPER", "LIVE"]
    status: Literal["OPEN", "CLOSED", "EXPIRED"] = "OPEN"
    run_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: ist_now().isoformat())

    entry_date: str
    entry_spot: float
    expiry_date: str
    dte_at_entry: int
    strategy: str
    sell_put_strike: Optional[int] = None
    buy_put_strike: Optional[int] = None
    sell_call_strike: Optional[int] = None
    buy_call_strike: Optional[int] = None
    entry_premium: float
    max_profit: float
    max_loss: float
    lower_breakeven: Optional[float] = None
    upper_breakeven: Optional[float] = None
    lot_size: int
    num_lots: int = 1

    iv_rank_at_entry: Optional[float] = None
    vix_at_entry: Optional[float] = None
    pop_at_entry: Optional[float] = None
    reward_risk_at_entry: Optional[float] = None
    theta_per_day_at_entry: Optional[float] = None
    rsi_at_entry: Optional[float] = None
    bb_width_at_entry: Optional[float] = None
    range_position_at_entry: Optional[str] = None
    spot_to_resistance_at_entry: Optional[float] = None
    spot_to_support_at_entry: Optional[float] = None

    exit_date: Optional[str] = None
    exit_spot: Optional[float] = None
    exit_premium: Optional[float] = None
    realized_pnl: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_triggered_by: Optional[str] = None
    win_loss: Optional[Literal["WIN", "LOSS", "BREAKEVEN"]] = None
    pnl_pct: Optional[float] = None
    held_days: Optional[int] = None
    notes: Optional[str] = None


class DailyMark(BaseModel):
    mark_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    trade_id: str
    mark_date: str
    spot: Optional[float] = None
    current_premium: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    pnl_pct_of_max_profit: Optional[float] = None
    dte_remaining: Optional[int] = None
    iv_rank_today: Optional[float] = None
    data_source: Literal["broker", "manual", "estimated"]
    exit_alert: str = "NONE"
    alert_detail: Optional[str] = None
    user_note: Optional[str] = None
    marked_at: str = Field(default_factory=lambda: ist_now().isoformat())


class TradeStats(BaseModel):
    total_trades: int = 0
    open_trades: int = 0
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate_pct: Optional[float] = None
    avg_win_pnl: Optional[float] = None
    avg_loss_pnl: Optional[float] = None
    expectancy: Optional[float] = None
    total_realized_pnl: float = 0.0
    paper_trades: int = 0
    live_trades: int = 0


class TradeOpenRequest(BaseModel):
    """Body for POST /trade/open — entry snapshot from last analysis / HITL."""

    trade_type: Literal["PAPER", "LIVE"]
    run_id: Optional[str] = None
    entry_date: Optional[str] = None
    entry_spot: float
    expiry_date: str
    dte_at_entry: int
    strategy: str
    sell_put_strike: Optional[int] = None
    buy_put_strike: Optional[int] = None
    sell_call_strike: Optional[int] = None
    buy_call_strike: Optional[int] = None
    entry_premium: float
    max_profit: float
    max_loss: float
    lower_breakeven: Optional[float] = None
    upper_breakeven: Optional[float] = None
    lot_size: int
    num_lots: int = 1
    iv_rank_at_entry: Optional[float] = None
    vix_at_entry: Optional[float] = None
    pop_at_entry: Optional[float] = None
    reward_risk_at_entry: Optional[float] = None
    theta_per_day_at_entry: Optional[float] = None
    rsi_at_entry: Optional[float] = None
    bb_width_at_entry: Optional[float] = None
    range_position_at_entry: Optional[str] = None
    spot_to_resistance_at_entry: Optional[float] = None
    spot_to_support_at_entry: Optional[float] = None
    notes: Optional[str] = None


class TradeMarkRequest(BaseModel):
    current_premium: Optional[float] = None
    spot: Optional[float] = None
    iv_rank: Optional[float] = None
    user_note: Optional[str] = None
    data_source: Literal["broker", "manual", "estimated"] = "manual"


class TradeCloseRequest(BaseModel):
    exit_premium: float
    exit_spot: float
    exit_reason: str
    exit_triggered_by: Literal["system_alert", "user_manual"] = "user_manual"
    notes: Optional[str] = None
