import config  # noqa: F401 — SSL + env before agents

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.market_data import market_data_agent
from agents.technical import technical_agent
from agents.options_chain import options_chain_agent
from agents.oi_analysis import oi_analysis_agent
from agents.greeks import greeks_agent
from agents.strategy import strategy_agent
from services.entry_guards import evaluator_skipped_sse_payload, should_skip_evaluator
from agents.evaluator import evaluator_agent
from models.strategy import OrderDetails, ExecutionResult
from services.openalgo_client import OpenAlgoService
from services.live_market_cache import get_cached_live_data, start_background_refresh
from services.error_messages import format_sse_error
from services.session_logger import SessionLogger
from services.journal_metrics import (
    build_master_journal,
    enrich_greeks,
    enrich_oi,
    enrich_strategy,
    enrich_technical,
)
from config import ENABLE_LIVE_ORDERS
from models.trade import TradeCloseRequest, TradeMarkRequest, TradeOpenRequest
from models.review import PendingReviewCompleteRequest, ReviewNowRequest
from services import trade_store
from services.expiry_utils import ist_now
from services.position_monitor import (
    auto_mark_open_trades,
    build_mark_for_trade,
    close_trade_record,
)
from services.position_review import (
    build_review_summary,
    complete_pending_review,
    run_position_review,
)
from services import review_store
from services.scheduler import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_background_refresh()
    trade_store.init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Nifty Options Advisor", version="1.0.0", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory state for HITL flow
_pending_strategy = {}
_pending_evaluation = {}
_pending_order_details = {}
_human_decision = {}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_dict(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return dict(obj)


async def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, default=str)}\n\n"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    svc = OpenAlgoService()
    funds = svc.get_funds()
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "openalgo_connected": svc._connected,
        "available_margin": funds.get("available_margin"),
    }


@app.get("/health/deep")
def deep_health():
    svc = OpenAlgoService()
    checks = {
        "openalgo_client": {"ok": svc._connected},
        "live_quote": {"ok": False},
        "option_chain": {"ok": False},
        "funds": {"ok": False},
        "live_orders_enabled": {"ok": ENABLE_LIVE_ORDERS},
    }

    try:
        market = svc.get_market_data()
        checks["live_quote"] = {
            "ok": not market.get("is_mock", True),
            "source": market.get("data_source", "unknown"),
            "nifty_spot": market.get("nifty_spot"),
            "vix": market.get("vix"),
        }
    except Exception as e:
        checks["live_quote"]["error"] = str(e)

    try:
        chain = svc.get_options_chain()
        checks["option_chain"] = {
            "ok": bool(not chain.empty and chain.attrs.get("source") != "mock"),
            "rows": int(len(chain)),
            "expiry": chain.attrs.get("expiry"),
            "source": chain.attrs.get("source", "unknown"),
        }
    except Exception as e:
        checks["option_chain"]["error"] = str(e)

    try:
        funds = svc.get_funds()
        checks["funds"] = {
            "ok": not funds.get("is_mock", False),
            "available_margin": funds.get("available_margin"),
        }
    except Exception as e:
        checks["funds"]["error"] = str(e)

    blocking = ["openalgo_client", "live_quote", "option_chain"]
    status = "ok" if all(checks[name]["ok"] for name in blocking) else "degraded"
    return {
        "status": status,
        "timestamp": datetime.now().isoformat(),
        "checks": checks,
    }


@app.get("/live-data")
def live_data():
    return get_cached_live_data()


@app.get("/analyse/stream")
async def analyse_stream():
    async def event_generator() -> AsyncIterator[str]:
        logger = SessionLogger()
        run_id = logger.get_run_id()

        agent_order = [
            "market_data",
            "technical",
            "options_chain",
            "oi_analysis",
            "greeks",
            "strategy",
            "evaluator",
        ]

        results = {}

        try:
            # ── Market Data ──────────────────────────────────────────────────
            yield await _sse_event({"agent": "market_data", "status": "running"})
            t0 = time.time()
            market = await asyncio.get_event_loop().run_in_executor(None, market_data_agent)
            elapsed = time.time() - t0
            logger.set_timing("market_data", elapsed)
            logger.log("market_data", _safe_dict(market), step=1)
            results["market_data"] = _safe_dict(market)
            yield await _sse_event({"agent": "market_data", "status": "done", "data": _safe_dict(market), "elapsed": round(elapsed, 2)})

            # ── Technical ────────────────────────────────────────────────────
            yield await _sse_event({"agent": "technical", "status": "running"})
            t0 = time.time()
            technical = await asyncio.get_event_loop().run_in_executor(None, technical_agent, market)
            elapsed = time.time() - t0
            logger.set_timing("technical", elapsed)
            technical_payload = enrich_technical(_safe_dict(technical))
            logger.log("technical", technical_payload, step=2)
            results["technical"] = technical_payload
            yield await _sse_event({"agent": "technical", "status": "done", "data": technical_payload, "elapsed": round(elapsed, 2)})

            # ── Options Chain ────────────────────────────────────────────────
            yield await _sse_event({"agent": "options_chain", "status": "running"})
            t0 = time.time()
            chain = await asyncio.get_event_loop().run_in_executor(None, options_chain_agent, market)
            elapsed = time.time() - t0
            logger.set_timing("options_chain", elapsed)
            chain_records = chain.to_dict("records")
            chain_payload = {
                "chain": chain_records,
                "source": chain.attrs.get("source", "unknown"),
                "expiry": chain.attrs.get("expiry"),
                "chain_exchange": chain.attrs.get("chain_exchange"),
            }
            logger.log("options_chain", chain_payload, step=3)
            results["options_chain"] = chain_payload
            yield await _sse_event({
                "agent": "options_chain",
                "status": "done",
                "data": {
                    "rows": len(chain_records),
                    "source": chain.attrs.get("source", "unknown"),
                    "expiry": chain.attrs.get("expiry"),
                    "chain_exchange": chain.attrs.get("chain_exchange"),
                },
                "elapsed": round(elapsed, 2),
            })

            # ── OI Analysis ──────────────────────────────────────────────────
            yield await _sse_event({"agent": "oi_analysis", "status": "running"})
            t0 = time.time()
            oi = await asyncio.get_event_loop().run_in_executor(None, oi_analysis_agent, chain, market)
            elapsed = time.time() - t0
            logger.set_timing("oi_analysis", elapsed)
            oi_payload = enrich_oi(_safe_dict(oi), market.nifty_spot)
            logger.log("oi_analysis", oi_payload, step=4)
            results["oi_analysis"] = oi_payload
            yield await _sse_event({"agent": "oi_analysis", "status": "done", "data": oi_payload, "elapsed": round(elapsed, 2)})

            # ── Greeks ───────────────────────────────────────────────────────
            yield await _sse_event({"agent": "greeks", "status": "running"})
            t0 = time.time()
            greeks = await asyncio.get_event_loop().run_in_executor(
                None, greeks_agent, chain, market, oi, technical
            )
            elapsed = time.time() - t0
            logger.set_timing("greeks", elapsed)
            greeks_raw = _safe_dict(greeks)
            build_stats = getattr(greeks, "candidate_build_stats", None) or {}
            rejected_itm = int(build_stats.get("candidates_rejected_itm", 0))
            greeks_payload = enrich_greeks(greeks_raw, market.nifty_spot, rejected_itm)
            logger.log("greeks", greeks_payload, step=5)
            results["greeks"] = greeks_payload
            yield await _sse_event({"agent": "greeks", "status": "done", "data": greeks_payload, "elapsed": round(elapsed, 2)})

            # ── Strategy ─────────────────────────────────────────────────────
            yield await _sse_event({"agent": "strategy", "status": "running"})
            funds = OpenAlgoService().get_funds()
            t0 = time.time()
            strategy = await asyncio.get_event_loop().run_in_executor(
                None, strategy_agent, market, technical, oi, greeks, chain, funds
            )
            elapsed = time.time() - t0
            logger.set_timing("strategy", elapsed)
            strategy_payload = enrich_strategy(
                _safe_dict(strategy),
                results["oi_analysis"],
                results["technical"],
                results["greeks"],
                market.nifty_spot,
            )
            logger.log("strategy", strategy_payload, step=6)
            results["strategy"] = strategy_payload
            _pending_strategy[run_id] = strategy
            yield await _sse_event({"agent": "strategy", "status": "done", "data": strategy_payload, "elapsed": round(elapsed, 2)})

            # ── Evaluator ─────────────────────────────────────────────────────
            evaluation = None
            if should_skip_evaluator(strategy):
                skipped = evaluator_skipped_sse_payload("pre_flight")
                logger.log("evaluator", skipped, step=7)
                results["evaluator"] = skipped
                yield await _sse_event(skipped)
            else:
                yield await _sse_event({"agent": "evaluator", "status": "running"})
                t0 = time.time()
                evaluation = await asyncio.get_event_loop().run_in_executor(
                    None, evaluator_agent, market, technical, oi, greeks, strategy, run_id, chain
                )
                elapsed = time.time() - t0
                logger.set_timing("evaluator", elapsed)
                logger.log("evaluator", _safe_dict(evaluation), step=7)
                results["evaluator"] = _safe_dict(evaluation)
                _pending_evaluation[run_id] = evaluation
                yield await _sse_event({
                    "agent": "evaluator",
                    "status": "done",
                    "data": _safe_dict(evaluation),
                    "elapsed": round(elapsed, 2),
                })

            # ── Final ────────────────────────────────────────────────────────
            journal = build_master_journal(logger.agent_outputs, market.nifty_spot)
            logger.save_master(journal=journal)

            # Build order details for HITL
            from datetime import datetime, timedelta
            expiry_str = chain.attrs.get("expiry")
            if not expiry_str:
                from services.expiry_utils import nifty_weekly_expiry_for_session

                expiry_str = nifty_weekly_expiry_for_session()

            from models.strategy import OrderLeg
            from services.chain_utils import order_quantity

            leg_qty = order_quantity(chain)
            legs = []
            if strategy.strategy != "WAIT":
                if strategy.sell_put_strike:
                    legs.append(OrderLeg(
                        symbol=f"NIFTY{expiry_str}{strategy.sell_put_strike}PE",
                        action="SELL", quantity=leg_qty,
                        premium=strategy.sell_put_premium or 0, leg="sell_put"
                    ))
                if strategy.buy_put_strike:
                    legs.append(OrderLeg(
                        symbol=f"NIFTY{expiry_str}{strategy.buy_put_strike}PE",
                        action="BUY", quantity=leg_qty,
                        premium=strategy.buy_put_premium or 0, leg="buy_put"
                    ))
                if strategy.sell_call_strike:
                    legs.append(OrderLeg(
                        symbol=f"NIFTY{expiry_str}{strategy.sell_call_strike}CE",
                        action="SELL", quantity=leg_qty,
                        premium=strategy.sell_call_premium or 0, leg="sell_call"
                    ))
                if strategy.buy_call_strike:
                    legs.append(OrderLeg(
                        symbol=f"NIFTY{expiry_str}{strategy.buy_call_strike}CE",
                        action="BUY", quantity=leg_qty,
                        premium=strategy.buy_call_premium or 0, leg="buy_call"
                    ))

            quality_score = evaluation.quality_score if evaluation is not None else 0.0
            order_details = OrderDetails(
                strategy=strategy.strategy,
                expiry=expiry_str,
                legs=legs,
                net_premium_actual=strategy.net_premium or 0,
                max_profit_actual=strategy.max_profit or 0,
                max_loss_actual=strategy.max_loss or 0,
                lower_breakeven=strategy.lower_breakeven,
                upper_breakeven=strategy.upper_breakeven,
                quality_score=quality_score,
            )
            _pending_order_details[run_id] = order_details

            yield await _sse_event({
                "agent": "complete",
                "run_id": run_id,
                "strategy": _safe_dict(strategy),
                "evaluation": _safe_dict(evaluation) if evaluation is not None else results.get("evaluator"),
                "order_details": _safe_dict(order_details),
            })

        except Exception as e:
            payload = format_sse_error(e)
            if not payload.get("detail"):
                payload["detail"] = str(e)
            yield await _sse_event(payload)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ApprovalRequest(BaseModel):
    run_id: str
    approved: bool
    reason: str = ""


@app.post("/approve")
def approve_order(request: ApprovalRequest):
    _human_decision[request.run_id] = {
        "approved": request.approved,
        "reason": request.reason,
        "timestamp": datetime.now().isoformat(),
    }
    return {"status": "recorded", "approved": request.approved}


class ExecuteRequest(BaseModel):
    run_id: str


@app.post("/execute")
def execute_order(request: ExecuteRequest):
    if not ENABLE_LIVE_ORDERS:
        raise HTTPException(
            status_code=403,
            detail="Live order execution is disabled. Set ENABLE_LIVE_ORDERS=true in backend .env to enable.",
        )

    decision = _human_decision.get(request.run_id)
    if not decision:
        raise HTTPException(status_code=400, detail="No human decision found for this run_id")
    if not decision["approved"]:
        raise HTTPException(status_code=400, detail="Trade was not approved")

    pending_strategy = _pending_strategy.get(request.run_id)
    if pending_strategy:
        blocked = getattr(pending_strategy, "integrity_blocked", False)
        trade_ready = getattr(pending_strategy, "data_trade_ready", True)
        if blocked or not trade_ready:
            raise HTTPException(
                status_code=403,
                detail="Trade blocked: live quotes and option chain required (integrity guardrail).",
            )

    order_details = _pending_order_details.get(request.run_id)
    if not order_details:
        raise HTTPException(status_code=400, detail="No order details found for this run_id")

    svc = OpenAlgoService()
    orders = []
    for leg in order_details.legs:
        result = svc.place_order(leg.model_dump())
        orders.append(result)

    return ExecutionResult(
        status="executed",
        timestamp=datetime.now().isoformat(),
        orders=orders,
        human_approved=True,
    ).model_dump()


@app.get("/history")
def history():
    logs_dir = Path("logs")
    runs = []
    if logs_dir.exists():
        for date_dir in sorted(logs_dir.iterdir(), reverse=True):
            if date_dir.is_dir():
                for run_dir in sorted(date_dir.iterdir(), reverse=True):
                    master = run_dir / "00_master.json"
                    if master.exists():
                        try:
                            with open(master) as f:
                                data = json.load(f)
                            runs.append({
                                "run_id": data.get("run_id"),
                                "timestamp": data.get("timestamp"),
                                "date": date_dir.name,
                                "strategy": data.get("agents", {}).get("strategy", {}).get("strategy"),
                                "quality_score": data.get("agents", {}).get("evaluator", {}).get("quality_score"),
                                "path": str(master),
                            })
                        except Exception:
                            pass
    return {"runs": runs}


# ── Trade tracking (paper / live lifecycle) ────────────────────────────────────


@app.post("/trade/open")
def trade_open(body: TradeOpenRequest):
    try:
        trade_id, warning = trade_store.open_trade_from_request(body)
        return {
            "trade_id": trade_id,
            "message": "Trade opened.",
            "warning": warning,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/trade/list")
def trade_list(trade_type: str | None = None, status: str | None = None):
    trades = trade_store.list_trades(trade_type=trade_type, status=status)
    stats = trade_store.get_trade_stats(trade_type=trade_type)
    return {
        "trades": [_safe_dict(t) for t in trades],
        "stats": stats.model_dump(),
    }


@app.get("/trade/stats")
def trade_stats(trade_type: str | None = None):
    return trade_store.get_trade_stats(trade_type=trade_type).model_dump()


@app.get("/trade/reviews/pending")
def trade_reviews_pending():
    pending = review_store.list_pending_reviews()
    return {"pending": [p.model_dump() for p in pending], "count": len(pending)}


@app.post("/trade/review-now")
def trade_review_now(body: ReviewNowRequest):
    try:
        return run_position_review(
            body.check_slot,
            trade_id=body.trade_id,
            bypass_session=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/trade/{trade_id}/reviews")
def trade_reviews_list(trade_id: str):
    trade = trade_store.get_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    reviews = review_store.list_reviews_for_trade(trade_id)
    return {"reviews": [r.model_dump() for r in reviews]}


@app.get("/trade/{trade_id}/reviews/latest")
def trade_reviews_latest(trade_id: str):
    trade = trade_store.get_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    latest = review_store.get_latest_completed_review(trade_id)
    if not latest:
        pending = review_store.get_latest_pending_review(trade_id)
        if pending:
            return {"review": pending.model_dump(), "status": "PENDING_INPUT"}
        raise HTTPException(status_code=404, detail="No reviews found")
    return {"review": latest.model_dump(), "status": "COMPLETED"}


@app.get("/trade/{trade_id}/review-summary")
def trade_review_summary(trade_id: str):
    try:
        return build_review_summary(trade_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/trade/{trade_id}/reviews/pending/complete")
def trade_pending_review_complete(trade_id: str, body: PendingReviewCompleteRequest):
    try:
        review = complete_pending_review(trade_id, body)
        return {
            "review": review.model_dump(),
            "exit_signal": review.exit_signal,
            "recommended_action": review.recommended_action,
            "reasoning": review.reasoning,
        }
    except ValueError as e:
        detail = str(e)
        try:
            parsed = json.loads(detail)
            raise HTTPException(status_code=400, detail=parsed) from e
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail=detail) from e


@app.get("/trade/{trade_id}")
def trade_get(trade_id: str):
    trade = trade_store.get_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    marks = trade_store.get_marks_for_trade(trade_id)
    latest = trade_store.get_latest_mark(trade_id)
    latest_alert = latest.exit_alert if latest else "NONE"
    return {
        "trade": trade.model_dump(),
        "marks": [m.model_dump() for m in marks],
        "latest_alert": latest_alert,
    }


@app.post("/trade/{trade_id}/mark")
def trade_mark(trade_id: str, body: TradeMarkRequest):
    trade = trade_store.get_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status != "OPEN":
        raise HTTPException(status_code=400, detail=f"Trade status is {trade.status}")

    today = ist_now().date().isoformat()
    if trade_store.get_mark_for_date(trade_id, today):
        raise HTTPException(
            status_code=400,
            detail=f"Mark already exists for {today}. Use a different date or close trade.",
        )

    spot = body.spot
    if spot is None:
        try:
            live = get_cached_live_data()
            spot = live.get("nifty_spot")
        except Exception:
            pass

    built = build_mark_for_trade(
        trade,
        today,
        current_premium=body.current_premium,
        spot=spot,
        iv_rank=body.iv_rank,
        data_source=body.data_source,
        user_note=body.user_note,
        fetch_if_missing=body.current_premium is None,
    )

    if built.get("needs_user_input"):
        return {
            "mark_id": None,
            "unrealized_pnl": None,
            "pnl_pct_of_max_profit": None,
            "exit_alert": "NONE",
            "alert_detail": None,
            "needs_user_input": True,
            "user_prompt": built.get("user_prompt"),
            "fetch_error": built.get("fetch_error"),
        }

    mark = built["mark"]
    mark_id = trade_store.add_daily_mark(mark)
    recommended = "CLOSE" if mark.exit_alert != "NONE" else "HOLD"
    return {
        "mark_id": mark_id,
        "unrealized_pnl": mark.unrealized_pnl,
        "pnl_pct_of_max_profit": mark.pnl_pct_of_max_profit,
        "exit_alert": mark.exit_alert,
        "alert_detail": mark.alert_detail,
        "needs_user_input": False,
        "user_prompt": None,
        "recommended_action": recommended,
    }


@app.post("/trade/mark-all")
def trade_mark_all():
    """Manual trigger: broker auto-mark all open trades for today (no full analysis required)."""
    spot = None
    iv_rank = None
    try:
        live = get_cached_live_data()
        spot = live.get("nifty_spot")
        iv_rank = live.get("iv_rank")
    except Exception:
        market = OpenAlgoService().get_market_data()
        spot = market.get("nifty_spot")
    if spot is None:
        raise HTTPException(status_code=503, detail="Spot price unavailable for marking")

    result = auto_mark_open_trades(float(spot), iv_rank=iv_rank)
    return {
        "agent": "trade_monitor",
        "open_trades_marked": result["marked"],
        "trades_needing_mark": result["trades_needing_mark"],
        "alerts": result["alerts"],
    }


@app.post("/trade/{trade_id}/close")
def trade_close(trade_id: str, body: TradeCloseRequest):
    trade = trade_store.get_trade(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.status != "OPEN":
        raise HTTPException(status_code=400, detail=f"Trade already {trade.status}")

    try:
        return close_trade_record(
            trade,
            body.exit_premium,
            body.exit_spot,
            body.exit_reason,
            body.exit_triggered_by,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
