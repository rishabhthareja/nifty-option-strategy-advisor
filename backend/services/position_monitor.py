"""Mark-to-market, broker premium fetch, and exit alerts for open trades."""

from __future__ import annotations

from typing import Any, List, Optional

import pandas as pd

from models.trade import DailyMark, TradeRecord
from services.expiry_utils import days_to_expiry, ist_now
from services.openalgo_client import OpenAlgoService
from services import trade_store


def calculate_unrealized_pnl(trade: TradeRecord, current_premium: float) -> float:
    """
    Credit strategy P&L (₹ total for position).

    entry_premium = net credit received (₹ per unit)
    current_premium = cost to close (debit, ₹ per unit)
    Profit when premium decays — never flip sign for spreads.
    """
    per_unit = float(trade.entry_premium) - float(current_premium)
    return round(per_unit * trade.lot_size * trade.num_lots, 2)


def calculate_pnl_pct_of_max_profit(
    trade: TradeRecord, unrealized_pnl: float
) -> Optional[float]:
    if not trade.max_profit or trade.max_profit <= 0:
        return None
    return round(unrealized_pnl / trade.max_profit * 100, 1)


def _leg_close_price(chain: pd.DataFrame, strike: int, side: str, action: str) -> float:
    """action: buy_to_close | sell_to_close for original position legs."""
    row = chain[chain["strike"] == strike]
    if row.empty:
        row = chain.iloc[(chain["strike"] - strike).abs().argsort()[:1]]
    r = row.iloc[0]
    prefix = "put" if side == "put" else "call"
    bid = float(r.get(f"{prefix}_bid") or 0)
    ask = float(r.get(f"{prefix}_ask") or 0)
    ltp = float(r.get(f"{prefix}_ltp") or 0)
    if action == "buy_to_close":
        if ask > 0:
            return ask
        return ltp
    if bid > 0:
        return bid
    return ltp


def fetch_current_premium(trade: TradeRecord) -> dict:
    """
    Fetch cost-to-close from OpenAlgo option chain for trade expiry + strikes.

    Returns dict with current_premium, data_source ('broker' or None), fetch_error.
    """
    try:
        svc = OpenAlgoService()
        if not svc._connected:
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "OpenAlgo not connected",
            }

        chain = svc.get_options_chain(expiry=trade.expiry_date)
        if chain is None or chain.empty:
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "Empty option chain",
            }
        if getattr(chain, "attrs", {}).get("source") == "mock":
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "Chain is mock — cannot mark LIVE from broker",
            }

        legs = []
        if trade.sell_put_strike and trade.buy_put_strike:
            legs.append(("put", trade.sell_put_strike, "buy_to_close"))
            legs.append(("put", trade.buy_put_strike, "sell_to_close"))
        if trade.sell_call_strike and trade.buy_call_strike:
            legs.append(("call", trade.sell_call_strike, "buy_to_close"))
            legs.append(("call", trade.buy_call_strike, "sell_to_close"))

        if not legs:
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "No leg strikes on trade",
            }

        total = 0.0
        for side, strike, action in legs:
            px = _leg_close_price(chain, int(strike), side, action)
            if action == "buy_to_close":
                total += px
            else:
                total -= px

        if total <= 0:
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "Could not price all legs",
            }

        return {
            "current_premium": round(total, 2),
            "data_source": "broker",
            "fetch_error": None,
        }
    except Exception as e:
        return {
            "current_premium": None,
            "data_source": None,
            "fetch_error": str(e),
        }


def evaluate_exit_conditions(trade: TradeRecord, mark: DailyMark) -> str:
    """
    Exit alert priority: BREACH > STOP_LOSS > DAY4 > PROFIT_TARGET > NONE.
    """
    spot = mark.spot
    if spot is not None:
        if trade.sell_call_strike and spot > trade.sell_call_strike:
            return "BREACH_CALL"
        if trade.sell_put_strike and spot < trade.sell_put_strike:
            return "BREACH_PUT"

    if mark.unrealized_pnl is not None and trade.max_loss:
        if mark.unrealized_pnl <= -float(trade.max_loss):
            return "STOP_LOSS_HIT"

    if mark.dte_remaining is not None and mark.dte_remaining <= 1:
        return "DAY4_CLOSE"

    if mark.unrealized_pnl is not None and trade.max_profit:
        if mark.unrealized_pnl >= float(trade.max_profit) * 0.50:
            return "PROFIT_TARGET_HIT"

    return "NONE"


def build_alert_detail(trade: TradeRecord, alert: str, mark: DailyMark) -> str:
    if alert == "NONE":
        return ""
    if alert == "BREACH_CALL":
        return (
            f"Spot {mark.spot} above short call {trade.sell_call_strike} — "
            "defend or close."
        )
    if alert == "BREACH_PUT":
        return (
            f"Spot {mark.spot} below short put {trade.sell_put_strike} — "
            "defend or close."
        )
    if alert == "STOP_LOSS_HIT":
        return (
            f"Unrealized P&L ₹{mark.unrealized_pnl:,.0f} at max loss "
            f"₹{trade.max_loss:,.0f} — consider closing."
        )
    if alert == "DAY4_CLOSE":
        return "1 day to expiry — close today to avoid gamma risk."
    if alert == "PROFIT_TARGET_HIT":
        return (
            f"Unrealized P&L ₹{mark.unrealized_pnl:,.0f} = "
            f"{mark.pnl_pct_of_max_profit:.0f}% of max profit ₹{trade.max_profit:,.0f}. "
            "Consider closing."
        )
    return alert


def paper_mark_user_prompt(trade: TradeRecord) -> str:
    legs = []
    if trade.sell_put_strike:
        legs.append(f"Sell {trade.sell_put_strike}PE")
    if trade.buy_put_strike:
        legs.append(f"Buy {trade.buy_put_strike}PE")
    if trade.sell_call_strike:
        legs.append(f"Sell {trade.sell_call_strike}CE")
    if trade.buy_call_strike:
        legs.append(f"Buy {trade.buy_call_strike}CE")
    leg_txt = " + ".join(legs) if legs else "all legs"
    return (
        f"Paper trade. Enter current market premium to close: {leg_txt} "
        f"({trade.strategy})."
    )


def build_mark_for_trade(
    trade: TradeRecord,
    mark_date: str,
    *,
    current_premium: Optional[float] = None,
    spot: Optional[float] = None,
    iv_rank: Optional[float] = None,
    data_source: str = "manual",
    user_note: Optional[str] = None,
    fetch_if_missing: bool = True,
) -> dict:
    """
    Build daily mark payload. Returns dict suitable for API response including
    needs_user_input when premium unknown.
    """
    premium = current_premium
    src = data_source
    fetch_error = None

    if premium is None and fetch_if_missing and trade.trade_type == "LIVE":
        fr = fetch_current_premium(trade)
        if fr.get("data_source") == "broker" and fr.get("current_premium") is not None:
            premium = fr["current_premium"]
            src = "broker"
        else:
            fetch_error = fr.get("fetch_error")

    if premium is None and trade.trade_type == "PAPER":
        return {
            "needs_user_input": True,
            "user_prompt": paper_mark_user_prompt(trade),
            "fetch_error": fetch_error,
        }

    if premium is None:
        msg = (
            "Broker unavailable. Enter current closing premium for your open "
            f"{trade.strategy} position."
        )
        return {
            "needs_user_input": True,
            "user_prompt": msg,
            "fetch_error": fetch_error,
        }

    unrealized = calculate_unrealized_pnl(trade, premium)
    dte = days_to_expiry(trade.expiry_date)
    mark = DailyMark(
        trade_id=trade.trade_id,
        mark_date=mark_date,
        spot=spot,
        current_premium=premium,
        unrealized_pnl=unrealized,
        pnl_pct_of_max_profit=calculate_pnl_pct_of_max_profit(trade, unrealized),
        dte_remaining=dte,
        iv_rank_today=iv_rank,
        data_source=src,
        user_note=user_note,
    )
    mark.exit_alert = evaluate_exit_conditions(trade, mark)
    mark.alert_detail = build_alert_detail(trade, mark.exit_alert, mark)

    return {
        "needs_user_input": False,
        "mark": mark,
        "unrealized_pnl": unrealized,
        "pnl_pct_of_max_profit": mark.pnl_pct_of_max_profit,
        "exit_alert": mark.exit_alert,
        "alert_detail": mark.alert_detail,
    }


def close_trade_record(
    trade: TradeRecord,
    exit_premium: float,
    exit_spot: float,
    exit_reason: str,
    exit_triggered_by: str,
    notes: Optional[str] = None,
    db_path=None,
) -> dict:
    """Close trade and persist realized P&L (₹ total)."""
    realized = calculate_unrealized_pnl(trade, exit_premium)
    pnl_pct = None
    if trade.max_profit and trade.max_profit > 0:
        pnl_pct = round(realized / trade.max_profit * 100, 1)

    if realized > 50:
        win_loss = "WIN"
    elif realized < -50:
        win_loss = "LOSS"
    else:
        win_loss = "BREAKEVEN"

    from datetime import datetime

    exit_date = ist_now().date().isoformat()
    held_days = None
    try:
        d0 = datetime.fromisoformat(trade.entry_date).date()
        d1 = datetime.fromisoformat(exit_date).date()
        held_days = (d1 - d0).days
    except ValueError:
        held_days = None

    trade_store.update_trade_status(
        trade.trade_id,
        "CLOSED",
        db_path,
        exit_date=exit_date,
        exit_spot=exit_spot,
        exit_premium=exit_premium,
        realized_pnl=realized,
        exit_reason=exit_reason,
        exit_triggered_by=exit_triggered_by,
        win_loss=win_loss,
        pnl_pct=pnl_pct,
        held_days=held_days,
        notes=notes or trade.notes,
    )

    return {
        "trade_id": trade.trade_id,
        "realized_pnl": realized,
        "win_loss": win_loss,
        "pnl_pct": pnl_pct,
        "message": f"Trade closed with {win_loss} (₹{realized:,.2f}).",
    }


def auto_mark_open_trades(
    spot: float,
    iv_rank: Optional[float] = None,
    db_path=None,
) -> dict:
    """
    Best-effort broker marks for all OPEN trades not yet marked today.
    Safe to call from analyse stream inside try/except.
    """
    today = ist_now().date().isoformat()
    open_trades = trade_store.list_trades(status="OPEN", db_path=db_path)
    marked = []
    needing = []
    alerts = []

    for trade in open_trades:
        if trade_store.get_mark_for_date(trade.trade_id, today, db_path):
            continue

        built = build_mark_for_trade(
            trade,
            today,
            spot=spot,
            iv_rank=iv_rank,
            fetch_if_missing=True,
        )
        if built.get("needs_user_input"):
            needing.append(trade.trade_id)
            continue

        mark = built["mark"]
        trade_store.add_daily_mark(mark, db_path)
        marked.append(trade.trade_id)
        if mark.exit_alert and mark.exit_alert != "NONE":
            alerts.append(
                {
                    "trade_id": trade.trade_id,
                    "alert": mark.exit_alert,
                    "detail": mark.alert_detail,
                    "recommended_action": "CLOSE",
                }
            )

    return {
        "marked": marked,
        "trades_needing_mark": needing,
        "alerts": alerts,
    }
