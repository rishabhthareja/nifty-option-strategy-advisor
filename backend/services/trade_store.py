"""SQLite persistence for paper/live trade tracking."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from models.trade import DailyMark, TradeOpenRequest, TradeRecord, TradeStats
from services.expiry_utils import days_to_expiry, ist_now

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "trades.db"

_TRADES_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    trade_type TEXT NOT NULL CHECK(trade_type IN ('PAPER','LIVE')),
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','CLOSED','EXPIRED')),
    run_id TEXT,
    created_at TEXT NOT NULL,
    entry_date TEXT NOT NULL,
    entry_spot REAL NOT NULL,
    expiry_date TEXT NOT NULL,
    dte_at_entry INTEGER NOT NULL,
    strategy TEXT NOT NULL,
    sell_put_strike INTEGER,
    buy_put_strike INTEGER,
    sell_call_strike INTEGER,
    buy_call_strike INTEGER,
    entry_premium REAL NOT NULL,
    max_profit REAL NOT NULL,
    max_loss REAL NOT NULL,
    lower_breakeven REAL,
    upper_breakeven REAL,
    lot_size INTEGER NOT NULL,
    num_lots INTEGER NOT NULL DEFAULT 1,
    iv_rank_at_entry REAL,
    vix_at_entry REAL,
    pop_at_entry REAL,
    reward_risk_at_entry REAL,
    theta_per_day_at_entry REAL,
    rsi_at_entry REAL,
    bb_width_at_entry REAL,
    range_position_at_entry TEXT,
    spot_to_resistance_at_entry REAL,
    spot_to_support_at_entry REAL,
    exit_date TEXT,
    exit_spot REAL,
    exit_premium REAL,
    realized_pnl REAL,
    exit_reason TEXT,
    exit_triggered_by TEXT,
    win_loss TEXT CHECK(win_loss IN ('WIN','LOSS','BREAKEVEN',NULL)),
    pnl_pct REAL,
    held_days INTEGER,
    notes TEXT
);
"""

_MARKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_marks (
    mark_id TEXT PRIMARY KEY,
    trade_id TEXT NOT NULL REFERENCES trades(trade_id),
    mark_date TEXT NOT NULL,
    spot REAL,
    current_premium REAL,
    unrealized_pnl REAL,
    pnl_pct_of_max_profit REAL,
    dte_remaining INTEGER,
    iv_rank_today REAL,
    data_source TEXT NOT NULL CHECK(data_source IN ('broker','manual','estimated')),
    exit_alert TEXT CHECK(exit_alert IN (
        'NONE','PROFIT_TARGET_HIT','STOP_LOSS_HIT',
        'DAY4_CLOSE','BREACH_PUT','BREACH_CALL'
    )),
    alert_detail TEXT,
    user_note TEXT,
    marked_at TEXT NOT NULL,
    UNIQUE(trade_id, mark_date)
);
"""


def _resolve_db_path(db_path: Optional[Path] = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env = os.environ.get("TRADE_DB_PATH")
    if env:
        return Path(env)
    return DEFAULT_DB_PATH


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = _resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(_TRADES_SCHEMA + _MARKS_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _row_to_trade(row: sqlite3.Row) -> TradeRecord:
    return TradeRecord(**dict(row))


def _row_to_mark(row: sqlite3.Row) -> DailyMark:
    return DailyMark(**dict(row))


def validate_trade_open(req: TradeOpenRequest) -> None:
    """Reject invalid open requests before insert."""
    if req.strategy == "WAIT":
        raise ValueError("Cannot open a trade for strategy WAIT.")

    if req.entry_premium <= 0:
        raise ValueError("entry_premium must be positive (net credit received).")
    if req.max_loss <= 0:
        raise ValueError("max_loss must be positive (₹ total risk for position).")
    if req.max_profit <= 0:
        raise ValueError("max_profit must be positive (₹ total max profit for position).")

    s = req.strategy
    if s in ("IRON_CONDOR", "IRON_BUTTERFLY"):
        for field in (
            "sell_put_strike",
            "buy_put_strike",
            "sell_call_strike",
            "buy_call_strike",
        ):
            if getattr(req, field) is None:
                raise ValueError(f"{s} requires all four leg strikes.")
    elif s == "BULL_PUT_SPREAD":
        if req.sell_put_strike is None or req.buy_put_strike is None:
            raise ValueError("BULL_PUT_SPREAD requires sell_put_strike and buy_put_strike.")
    elif s == "BEAR_CALL_SPREAD":
        if req.sell_call_strike is None or req.buy_call_strike is None:
            raise ValueError("BEAR_CALL_SPREAD requires sell_call_strike and buy_call_strike.")
    elif s == "SHORT_STRANGLE":
        if req.sell_put_strike is None or req.sell_call_strike is None:
            raise ValueError("SHORT_STRANGLE requires sell_put_strike and sell_call_strike.")


def find_open_trade_by_run_id(
    run_id: str, db_path: Optional[Path] = None
) -> Optional[TradeRecord]:
    if not run_id:
        return None
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM trades WHERE run_id = ? AND status = 'OPEN' LIMIT 1",
            (run_id,),
        ).fetchone()
        return _row_to_trade(row) if row else None
    finally:
        conn.close()


def create_trade(trade: TradeRecord, db_path: Optional[Path] = None) -> str:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        data = trade.model_dump()
        cols = list(data.keys())
        placeholders = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO trades ({','.join(cols)}) VALUES ({placeholders})",
            [data[c] for c in cols],
        )
        conn.commit()
        return trade.trade_id
    finally:
        conn.close()


def open_trade_from_request(
    req: TradeOpenRequest, db_path: Optional[Path] = None
) -> tuple[str, Optional[str]]:
    """
    Validate and create trade. Returns (trade_id, warning_message).
    Duplicate open run_id returns existing trade_id with warning.
    """
    validate_trade_open(req)
    if req.run_id:
        existing = find_open_trade_by_run_id(req.run_id, db_path)
        if existing:
            return (
                existing.trade_id,
                f"Open trade already exists for run_id {req.run_id}: {existing.trade_id}",
            )

    entry_date = req.entry_date or ist_now().date().isoformat()
    trade = TradeRecord(
        trade_type=req.trade_type,
        run_id=req.run_id,
        entry_date=entry_date,
        entry_spot=req.entry_spot,
        expiry_date=req.expiry_date,
        dte_at_entry=req.dte_at_entry,
        strategy=req.strategy,
        sell_put_strike=req.sell_put_strike,
        buy_put_strike=req.buy_put_strike,
        sell_call_strike=req.sell_call_strike,
        buy_call_strike=req.buy_call_strike,
        entry_premium=req.entry_premium,
        max_profit=req.max_profit,
        max_loss=req.max_loss,
        lower_breakeven=req.lower_breakeven,
        upper_breakeven=req.upper_breakeven,
        lot_size=req.lot_size,
        num_lots=req.num_lots,
        iv_rank_at_entry=req.iv_rank_at_entry,
        vix_at_entry=req.vix_at_entry,
        pop_at_entry=req.pop_at_entry,
        reward_risk_at_entry=req.reward_risk_at_entry,
        theta_per_day_at_entry=req.theta_per_day_at_entry,
        rsi_at_entry=req.rsi_at_entry,
        bb_width_at_entry=req.bb_width_at_entry,
        range_position_at_entry=req.range_position_at_entry,
        spot_to_resistance_at_entry=req.spot_to_resistance_at_entry,
        spot_to_support_at_entry=req.spot_to_support_at_entry,
        notes=req.notes,
    )
    tid = create_trade(trade, db_path)
    return tid, None


def get_trade(trade_id: str, db_path: Optional[Path] = None) -> Optional[TradeRecord]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        return _row_to_trade(row) if row else None
    finally:
        conn.close()


def list_trades(
    trade_type: Optional[str] = None,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[TradeRecord]:
    conn = get_connection(db_path)
    try:
        q = "SELECT * FROM trades WHERE 1=1"
        params: list = []
        if trade_type:
            q += " AND trade_type = ?"
            params.append(trade_type)
        if status:
            q += " AND status = ?"
            params.append(status)
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
        return [_row_to_trade(r) for r in rows]
    finally:
        conn.close()


def update_trade_status(
    trade_id: str, status: str, db_path: Optional[Path] = None, **exit_fields
) -> None:
    conn = get_connection(db_path)
    try:
        exit_fields["status"] = status
        sets = ", ".join(f"{k} = ?" for k in exit_fields)
        vals = list(exit_fields.values()) + [trade_id]
        conn.execute(f"UPDATE trades SET {sets} WHERE trade_id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def add_daily_mark(mark: DailyMark, db_path: Optional[Path] = None) -> str:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        existing = conn.execute(
            "SELECT mark_id FROM daily_marks WHERE trade_id = ? AND mark_date = ?",
            (mark.trade_id, mark.mark_date),
        ).fetchone()
        if existing:
            raise ValueError(
                f"Mark already exists for trade {mark.trade_id} on {mark.mark_date}"
            )

        data = mark.model_dump()
        cols = list(data.keys())
        placeholders = ",".join("?" * len(cols))
        conn.execute(
            f"INSERT INTO daily_marks ({','.join(cols)}) VALUES ({placeholders})",
            [data[c] for c in cols],
        )

        trade = get_trade(mark.trade_id, db_path)
        if trade and trade.status == "OPEN" and mark.dte_remaining is not None:
            if mark.dte_remaining < 0:
                update_trade_status(trade.trade_id, "EXPIRED", db_path)

        conn.commit()
        return mark.mark_id
    finally:
        conn.close()


def get_marks_for_trade(
    trade_id: str, db_path: Optional[Path] = None
) -> List[DailyMark]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM daily_marks WHERE trade_id = ? ORDER BY mark_date",
            (trade_id,),
        ).fetchall()
        return [_row_to_mark(r) for r in rows]
    finally:
        conn.close()


def get_latest_mark(
    trade_id: str, db_path: Optional[Path] = None
) -> Optional[DailyMark]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM daily_marks WHERE trade_id = ? ORDER BY mark_date DESC LIMIT 1",
            (trade_id,),
        ).fetchone()
        return _row_to_mark(row) if row else None
    finally:
        conn.close()


def get_mark_for_date(
    trade_id: str, mark_date: str, db_path: Optional[Path] = None
) -> Optional[DailyMark]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM daily_marks WHERE trade_id = ? AND mark_date = ?",
            (trade_id, mark_date),
        ).fetchone()
        return _row_to_mark(row) if row else None
    finally:
        conn.close()


def get_trade_stats(
    trade_type: Optional[str] = None, db_path: Optional[Path] = None
) -> TradeStats:
    conn = get_connection(db_path)
    try:
        base = "FROM trades WHERE 1=1"
        params: list = []
        if trade_type:
            base += " AND trade_type = ?"
            params.append(trade_type)

        total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
        open_n = conn.execute(
            f"SELECT COUNT(*) {base} AND status = 'OPEN'", params
        ).fetchone()[0]
        closed_n = conn.execute(
            f"SELECT COUNT(*) {base} AND status = 'CLOSED'", params
        ).fetchone()[0]
        paper_n = conn.execute(
            f"SELECT COUNT(*) {base} AND trade_type = 'PAPER'", params
        ).fetchone()[0]
        live_n = conn.execute(
            f"SELECT COUNT(*) {base} AND trade_type = 'LIVE'", params
        ).fetchone()[0]

        closed_base = base + " AND status = 'CLOSED'"
        wins = conn.execute(
            f"SELECT COUNT(*) {closed_base} AND win_loss = 'WIN'", params
        ).fetchone()[0]
        losses = conn.execute(
            f"SELECT COUNT(*) {closed_base} AND win_loss = 'LOSS'", params
        ).fetchone()[0]
        total_pnl = conn.execute(
            f"SELECT COALESCE(SUM(realized_pnl), 0) {closed_base}", params
        ).fetchone()[0]
        avg_win = conn.execute(
            f"SELECT AVG(realized_pnl) {closed_base} AND win_loss = 'WIN'", params
        ).fetchone()[0]
        avg_loss = conn.execute(
            f"SELECT AVG(realized_pnl) {closed_base} AND win_loss = 'LOSS'", params
        ).fetchone()[0]

        win_rate = round(wins / closed_n * 100, 1) if closed_n else None
        expectancy = None
        if closed_n and avg_win is not None and avg_loss is not None:
            loss_rate = losses / closed_n
            win_r = wins / closed_n
            expectancy = round(
                (win_r * (avg_win or 0)) + (loss_rate * (avg_loss or 0)), 2
            )

        return TradeStats(
            total_trades=total,
            open_trades=open_n,
            closed_trades=closed_n,
            wins=wins,
            losses=losses,
            win_rate_pct=win_rate,
            avg_win_pnl=round(avg_win, 2) if avg_win is not None else None,
            avg_loss_pnl=round(avg_loss, 2) if avg_loss is not None else None,
            expectancy=expectancy,
            total_realized_pnl=round(float(total_pnl), 2),
            paper_trades=paper_n,
            live_trades=live_n,
        )
    finally:
        conn.close()
