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


def _extract_position_greeks(chain: pd.DataFrame, trade: TradeRecord) -> dict:
    """Extract current net Greeks from chain for the 4 position legs."""

    def get_greek(strike, side, col):
        if strike is None:
            return None
        prefix = "put" if side == "put" else "call"
        row = chain[chain["strike"] == strike]
        if row.empty:
            row = chain.iloc[(chain["strike"] - strike).abs().argsort()[:1]]
        try:
            return float(row.iloc[0].get(f"{prefix}_{col}") or 0)
        except (TypeError, ValueError):
            return None

    sc_delta = get_greek(trade.sell_call_strike, "call", "delta")
    sp_delta = get_greek(trade.sell_put_strike, "put", "delta")
    bc_delta = get_greek(trade.buy_call_strike, "call", "delta")
    bp_delta = get_greek(trade.buy_put_strike, "put", "delta")

    sc_theta = get_greek(trade.sell_call_strike, "call", "theta")
    sp_theta = get_greek(trade.sell_put_strike, "put", "theta")
    bc_theta = get_greek(trade.buy_call_strike, "call", "theta")
    bp_theta = get_greek(trade.buy_put_strike, "put", "theta")

    deltas = [d for d in [sc_delta, sp_delta, bc_delta, bp_delta] if d is not None]
    net_theta = 0.0
    for val, sign in [(sc_theta, -1), (sp_theta, -1), (bc_theta, 1), (bp_theta, 1)]:
        if val is not None:
            net_theta += sign * val

    return {
        "sell_call_delta": sc_delta,
        "sell_put_delta": sp_delta,
        "net_delta": round(sum(deltas), 4) if deltas else None,
        "net_theta": round(net_theta, 2),
    }


def _price_legs_from_chain(chain: pd.DataFrame, trade: TradeRecord) -> Optional[float]:
    legs = []
    if trade.sell_put_strike and trade.buy_put_strike:
        legs.append(("put", trade.sell_put_strike, "buy_to_close"))
        legs.append(("put", trade.buy_put_strike, "sell_to_close"))
    if trade.sell_call_strike and trade.buy_call_strike:
        legs.append(("call", trade.sell_call_strike, "buy_to_close"))
        legs.append(("call", trade.buy_call_strike, "sell_to_close"))
    if not legs:
        return None

    total = 0.0
    for side, strike, action in legs:
        px = _leg_close_price(chain, int(strike), side, action)
        if action == "buy_to_close":
            total += px
        else:
            total -= px
    if total < 0:
        return None
    return round(total, 2)


def fetch_current_premium(
    trade: TradeRecord,
    chain: pd.DataFrame | None = None,
) -> dict:
    """
    Fetch cost-to-close from pre-fetched chain or OpenAlgo for trade expiry + strikes.

    Returns dict with current_premium, data_source, fetch_error, optional current_greeks.
    """
    if chain is not None and not chain.empty:
        is_mock = getattr(chain, "attrs", {}).get("source") == "mock"
        if is_mock:
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "Chain is mock — skipping",
                "is_mock": True,
            }

        total = _price_legs_from_chain(chain, trade)
        if total is None:
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "Could not price all legs from chain",
            }

        greeks = _extract_position_greeks(chain, trade)
        return {
            "current_premium": total,
            "data_source": "chain_ltp",
            "fetch_error": None,
            "is_mock": False,
            "current_greeks": greeks,
        }

    try:
        svc = OpenAlgoService()
        if not svc._connected:
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "OpenAlgo not connected",
            }

        broker_chain = svc.get_options_chain(expiry=trade.expiry_date)
        if broker_chain is None or broker_chain.empty:
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "Empty option chain",
            }
        if getattr(broker_chain, "attrs", {}).get("source") == "mock":
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "Chain is mock — cannot mark LIVE from broker",
            }

        total = _price_legs_from_chain(broker_chain, trade)
        if total is None:
            return {
                "current_premium": None,
                "data_source": None,
                "fetch_error": "Could not price all legs",
            }

        return {
            "current_premium": total,
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

    if premium is None and fetch_if_missing:
        fr = fetch_current_premium(trade)
        src_name = fr.get("data_source")
        if src_name in ("broker", "chain_ltp") and fr.get("current_premium") is not None:
            premium = fr["current_premium"]
            src = src_name
        elif trade.trade_type == "LIVE":
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


def list_open_live_trades_for_monitoring() -> List[TradeRecord]:
    """OPEN LIVE trades with expiry on or after today (non-expired)."""
    open_live = trade_store.list_trades(trade_type="LIVE", status="OPEN")
    return [t for t in open_live if days_to_expiry(t.expiry_date) >= 0]


def auto_create_paper_trade(
    strategy,
    oi,
    greeks,
    chain: pd.DataFrame,
    market,
    run_id: str,
    technical=None,
) -> dict:
    """
    Auto-create a paper trade from a completed strategy recommendation.
    Returns {"trade_id": str} on success or {"skipped": reason} if not applicable.
    """
    from config import AUTO_PAPER_TRADE
    from models.trade import TradeOpenRequest
    from services.chain_utils import lot_size_from_chain
    from services.trade_entry_snapshot import entry_enrichment_from_agents

    if not AUTO_PAPER_TRADE:
        return {"skipped": "AUTO_PAPER_TRADE disabled"}

    if strategy.strategy == "WAIT":
        return {"skipped": "Strategy is WAIT"}

    entry_premium = getattr(strategy, "conservative_net_premium", None) or strategy.net_premium
    if not entry_premium or entry_premium <= 0:
        return {"skipped": "No valid entry premium"}

    expiry = strategy.option_expiry or getattr(chain, "attrs", {}).get("expiry")
    if not expiry:
        return {"skipped": "No expiry on strategy"}

    open_papers = trade_store.list_trades(trade_type="PAPER", status="OPEN")
    for t in open_papers:
        if (
            t.expiry_date == expiry
            and t.sell_call_strike == strategy.sell_call_strike
            and t.sell_put_strike == strategy.sell_put_strike
        ):
            return {"skipped": f"Duplicate paper trade already open: {t.trade_id}"}

    def get_ltp(strike, side):
        if strike is None or chain is None or chain.empty:
            return None
        prefix = "put" if side == "put" else "call"
        row = chain[chain["strike"] == strike]
        if row.empty:
            return None
        try:
            return float(row.iloc[0].get(f"{prefix}_ltp") or 0)
        except (TypeError, ValueError):
            return None

    enrichment = entry_enrichment_from_agents(technical=technical, oi=oi, greeks=greeks)

    req = TradeOpenRequest(
        trade_type="PAPER",
        run_id=run_id,
        entry_spot=market.nifty_spot,
        expiry_date=expiry,
        dte_at_entry=strategy.days_to_expiry or 0,
        strategy=strategy.strategy,
        sell_put_strike=strategy.sell_put_strike,
        buy_put_strike=strategy.buy_put_strike,
        sell_call_strike=strategy.sell_call_strike,
        buy_call_strike=strategy.buy_call_strike,
        entry_premium=entry_premium,
        max_profit=getattr(strategy, "conservative_max_profit", None) or strategy.max_profit or 0,
        max_loss=getattr(strategy, "conservative_max_loss", None) or strategy.max_loss or 0,
        lower_breakeven=getattr(strategy, "conservative_lower_breakeven", None)
        or strategy.lower_breakeven,
        upper_breakeven=getattr(strategy, "conservative_upper_breakeven", None)
        or strategy.upper_breakeven,
        lot_size=lot_size_from_chain(chain),
        num_lots=1,
        iv_rank_at_entry=getattr(strategy, "iv_rank_snapshot", None),
        vix_at_entry=market.vix,
        pop_at_entry=strategy.est_pop_pct,
        reward_risk_at_entry=strategy.reward_risk,
        theta_per_day_at_entry=getattr(strategy, "theta_per_day", None),
        pcr_at_entry=getattr(strategy, "pcr_snapshot", None),
        range_position_at_entry=getattr(strategy, "range_position", None),
        spot_to_resistance_at_entry=getattr(strategy, "spot_to_resistance_pts", None),
        spot_to_support_at_entry=getattr(strategy, "spot_to_support_pts", None),
        sell_put_entry_ltp=get_ltp(strategy.sell_put_strike, "put"),
        sell_call_entry_ltp=get_ltp(strategy.sell_call_strike, "call"),
        buy_put_entry_ltp=get_ltp(strategy.buy_put_strike, "put"),
        buy_call_entry_ltp=get_ltp(strategy.buy_call_strike, "call"),
        **enrichment,
    )

    trade_id, warning = trade_store.open_trade_from_request(req)
    return {"trade_id": trade_id, "warning": warning}
