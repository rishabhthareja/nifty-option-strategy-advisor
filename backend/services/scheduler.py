"""APScheduler jobs for position reviews (IST)."""

from __future__ import annotations

import logging
from itertools import groupby

from apscheduler.schedulers.background import BackgroundScheduler

from services.position_review import is_trading_session, run_position_review

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _job_morning():
    logger.info("Scheduler: MORNING position review")
    run_position_review("MORNING")


def _job_midday():
    logger.info("Scheduler: MIDDAY position review")
    run_position_review("MIDDAY")


def _job_eod():
    logger.info("Scheduler: EOD position review")
    run_position_review("EOD")


def auto_refresh_paper_pnl():
    """
    Hourly P&L refresh for open paper trades using live chain prices.
    Never runs on mock data. Never raises — background job.
    """
    if not is_trading_session():
        return
    try:
        from models.trade import DailyMark
        from services.expiry_utils import days_to_expiry, ist_now
        from services.openalgo_client import OpenAlgoService
        from services import trade_store
        from services.position_monitor import (
            build_alert_detail,
            calculate_pnl_pct_of_max_profit,
            calculate_unrealized_pnl,
            evaluate_exit_conditions,
            fetch_current_premium,
        )

        svc = OpenAlgoService()
        if not svc._connected:
            return

        live = svc.get_live_data()
        spot = float((live or {}).get("nifty_spot") or 0)
        if spot <= 0:
            return

        paper_trades = trade_store.list_trades(trade_type="PAPER", status="OPEN")
        if not paper_trades:
            return

        for expiry, group in groupby(
            sorted(paper_trades, key=lambda t: t.expiry_date),
            key=lambda t: t.expiry_date,
        ):
            chain = svc.get_options_chain(expiry=expiry)
            if chain is None or chain.empty:
                continue
            if getattr(chain, "attrs", {}).get("source") == "mock":
                continue

            for trade in group:
                try:
                    result = fetch_current_premium(trade, chain=chain)
                    if result.get("current_premium") is None:
                        continue

                    pnl = calculate_unrealized_pnl(trade, result["current_premium"])
                    pnl_pct = calculate_pnl_pct_of_max_profit(trade, pnl)
                    now = ist_now()

                    mark = DailyMark(
                        trade_id=trade.trade_id,
                        mark_date=now.date().isoformat(),
                        spot=spot,
                        current_premium=result["current_premium"],
                        unrealized_pnl=pnl,
                        pnl_pct_of_max_profit=pnl_pct,
                        dte_remaining=days_to_expiry(trade.expiry_date),
                        data_source="chain_ltp",
                    )
                    mark.exit_alert = evaluate_exit_conditions(trade, mark)
                    mark.alert_detail = build_alert_detail(trade, mark.exit_alert, mark)
                    trade_store.upsert_daily_mark(mark)

                except Exception as e:
                    logger.warning(
                        "Hourly paper refresh failed for %s: %s", trade.trade_id, e
                    )

    except Exception as e:
        logger.error("auto_refresh_paper_pnl error: %s", e)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    _scheduler.add_job(
        _job_morning,
        "cron",
        hour=10,
        minute=30,
        id="position_review_morning",
        replace_existing=True,
    )
    _scheduler.add_job(
        _job_midday,
        "cron",
        hour=13,
        minute=30,
        id="position_review_midday",
        replace_existing=True,
    )
    _scheduler.add_job(
        _job_eod,
        "cron",
        hour=15,
        minute=0,
        id="position_review_eod",
        replace_existing=True,
    )
    _scheduler.add_job(
        auto_refresh_paper_pnl,
        "cron",
        hour="10,11,12,13,14",
        minute=30,
        id="paper_pnl_hourly",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Position review scheduler started (10:30, 13:30, 15:00 IST; paper P&L hourly)"
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Position review scheduler stopped")
