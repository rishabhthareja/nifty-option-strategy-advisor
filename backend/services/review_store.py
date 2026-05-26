"""SQLite persistence for position reviews."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from models.review import PositionReview

from services.trade_store import DEFAULT_DB_PATH, get_connection, init_db

_REVIEWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_reviews (
    review_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL REFERENCES trades(trade_id),
    review_time TEXT NOT NULL,
    review_date TEXT NOT NULL,
    check_slot TEXT NOT NULL CHECK(check_slot IN ('MORNING','MIDDAY','EOD')),
    review_status TEXT NOT NULL CHECK(review_status IN ('COMPLETED','PENDING_INPUT','SKIPPED')),
    exit_signal TEXT,
    exit_reason_codes TEXT,
    recommended_action TEXT,
    reasoning TEXT,
    reasoning_skipped INTEGER DEFAULT 0,
    alert_detail TEXT,
    current_spot REAL,
    current_premium REAL,
    unrealized_pnl REAL,
    pnl_pct_of_max_profit REAL,
    dte_remaining INTEGER,
    data_source TEXT,
    iv_rank_today REAL,
    vix_today REAL,
    rsi_today REAL,
    bb_width_today REAL,
    pcr_today REAL,
    range_position_today TEXT,
    cushion_call_entry REAL,
    cushion_call_today REAL,
    cushion_put_entry REAL,
    cushion_put_today REAL,
    call_oi_at_entry REAL,
    call_oi_today REAL,
    call_oi_change_pct REAL,
    call_oi_signal TEXT,
    put_oi_at_entry REAL,
    put_oi_today REAL,
    put_oi_change_pct REAL,
    put_oi_signal TEXT,
    max_pain_at_entry REAL,
    max_pain_today REAL,
    max_pain_shift_pts REAL,
    max_pain_signal TEXT,
    short_call_delta_entry REAL,
    short_call_delta_today REAL,
    short_put_delta_entry REAL,
    short_put_delta_today REAL,
    net_delta_today REAL,
    net_theta_entry REAL,
    net_theta_today REAL,
    net_vega_today REAL,
    gamma_risk_level TEXT,
    delta_signal TEXT,
    theta_signal TEXT,
    vega_signal TEXT,
    snapshot_raw TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(trade_id, check_slot, review_date)
);
"""

_REVIEW_MIGRATION_COLUMNS = [
    ("move_vs_expected", "REAL"),
    ("move_class", "TEXT"),
    ("daily_expected_move", "REAL"),
    ("theta_delta_ratio", "REAL"),
    ("theta_compensating", "INTEGER"),
    ("oi_call_classification", "TEXT"),
    ("oi_call_confidence", "REAL"),
    ("oi_put_classification", "TEXT"),
    ("oi_put_confidence", "REAL"),
    ("action_category", "TEXT"),
    ("action_confidence", "TEXT"),
    ("evidence_list", "TEXT"),
    ("pending_greeks", "INTEGER DEFAULT 0"),
]


def _migrate_review_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(position_reviews)").fetchall()}
    for col, col_type in _REVIEW_MIGRATION_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE position_reviews ADD COLUMN {col} {col_type}")


def _row_to_review(row: sqlite3.Row) -> PositionReview:
    data = dict(row)
    codes = data.get("exit_reason_codes")
    if isinstance(codes, str):
        try:
            data["exit_reason_codes"] = json.loads(codes) if codes else []
        except json.JSONDecodeError:
            data["exit_reason_codes"] = []
    if data.get("theta_compensating") is not None:
        data["theta_compensating"] = bool(data["theta_compensating"])
    if data.get("pending_greeks") is not None:
        data["pending_greeks"] = bool(data["pending_greeks"])
    ev = data.get("evidence_list")
    if isinstance(ev, str):
        try:
            data["evidence_list"] = json.loads(ev) if ev else []
        except json.JSONDecodeError:
            data["evidence_list"] = []
    data["reasoning_skipped"] = bool(data.get("reasoning_skipped"))
    return PositionReview(**data)


def _review_to_row(review: PositionReview) -> dict:
    data = review.model_dump()
    data["exit_reason_codes"] = json.dumps(data.get("exit_reason_codes") or [])
    data["evidence_list"] = json.dumps(data.get("evidence_list") or [])
    if data.get("theta_compensating") is not None:
        data["theta_compensating"] = 1 if data["theta_compensating"] else 0
    data["pending_greeks"] = 1 if data.get("pending_greeks") else 0
    data["reasoning_skipped"] = 1 if data.get("reasoning_skipped") else 0
    return data


def upsert_review(review: PositionReview, db_path: Optional[Path] = None) -> str:
    init_db(db_path)
    data = _review_to_row(review)
    cols = list(data.keys())
    placeholders = ",".join("?" * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "review_id")
    conn = get_connection(db_path)
    try:
        conn.execute(
            f"""
            INSERT INTO position_reviews ({','.join(cols)})
            VALUES ({placeholders})
            ON CONFLICT(trade_id, check_slot, review_date) DO UPDATE SET {updates}
            """,
            [data[c] for c in cols],
        )
        conn.commit()
        return review.review_id
    finally:
        conn.close()


def get_review(review_id: str, db_path: Optional[Path] = None) -> Optional[PositionReview]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM position_reviews WHERE review_id = ?", (review_id,)
        ).fetchone()
        return _row_to_review(row) if row else None
    finally:
        conn.close()


def list_reviews_for_trade(
    trade_id: str, db_path: Optional[Path] = None
) -> List[PositionReview]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM position_reviews
            WHERE trade_id = ?
            ORDER BY review_time DESC
            """,
            (trade_id,),
        ).fetchall()
        return [_row_to_review(r) for r in rows]
    finally:
        conn.close()


def get_latest_completed_review(
    trade_id: str, db_path: Optional[Path] = None
) -> Optional[PositionReview]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM position_reviews
            WHERE trade_id = ? AND review_status = 'COMPLETED'
            ORDER BY review_time DESC
            LIMIT 1
            """,
            (trade_id,),
        ).fetchone()
        return _row_to_review(row) if row else None
    finally:
        conn.close()


def get_latest_pending_review(
    trade_id: str, db_path: Optional[Path] = None
) -> Optional[PositionReview]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM position_reviews
            WHERE trade_id = ? AND review_status = 'PENDING_INPUT'
            ORDER BY review_time DESC
            LIMIT 1
            """,
            (trade_id,),
        ).fetchone()
        return _row_to_review(row) if row else None
    finally:
        conn.close()


def list_pending_reviews(db_path: Optional[Path] = None) -> List[PositionReview]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM position_reviews
            WHERE review_status = 'PENDING_INPUT'
            ORDER BY review_time DESC
            """
        ).fetchall()
        return [_row_to_review(r) for r in rows]
    finally:
        conn.close()


def get_review_for_slot_date(
    trade_id: str,
    check_slot: str,
    review_date: str,
    db_path: Optional[Path] = None,
) -> Optional[PositionReview]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM position_reviews
            WHERE trade_id = ? AND check_slot = ? AND review_date = ?
            """,
            (trade_id, check_slot, review_date),
        ).fetchone()
        return _row_to_review(row) if row else None
    finally:
        conn.close()


def get_midday_review_today(
    trade_id: str, review_date: str, db_path: Optional[Path] = None
) -> Optional[PositionReview]:
    return get_review_for_slot_date(trade_id, "MIDDAY", review_date, db_path)


def mark_pending_as_skipped(
    trade_id: str,
    before_slot: str,
    review_date: str,
    db_path: Optional[Path] = None,
) -> int:
    """Mark PENDING_INPUT rows for this trade/date as SKIPPED when a new slot runs."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE position_reviews
            SET review_status = 'SKIPPED',
                alert_detail = COALESCE(alert_detail, '') || ' Skipped: new slot ' || ?
            WHERE trade_id = ? AND review_date = ? AND review_status = 'PENDING_INPUT'
            """,
            (before_slot, trade_id, review_date),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
