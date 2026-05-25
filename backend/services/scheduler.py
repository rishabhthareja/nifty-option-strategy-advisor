"""APScheduler jobs for position reviews (IST)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from services.position_review import run_position_review

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
    _scheduler.start()
    logger.info("Position review scheduler started (10:30, 13:30, 15:00 IST)")
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Position review scheduler stopped")
