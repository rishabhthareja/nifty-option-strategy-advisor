import config  # noqa: F401 — SSL + env before agents

import asyncio
import json
import os
import time
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
from config import ENABLE_LIVE_ORDERS

app = FastAPI(title="Nifty Options Advisor", version="1.0.0")


@app.on_event("startup")
def _startup_live_cache():
    start_background_refresh()


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
            logger.log("technical", _safe_dict(technical), step=2)
            results["technical"] = _safe_dict(technical)
            yield await _sse_event({"agent": "technical", "status": "done", "data": _safe_dict(technical), "elapsed": round(elapsed, 2)})

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
            logger.log("oi_analysis", _safe_dict(oi), step=4)
            results["oi_analysis"] = _safe_dict(oi)
            yield await _sse_event({"agent": "oi_analysis", "status": "done", "data": _safe_dict(oi), "elapsed": round(elapsed, 2)})

            # ── Greeks ───────────────────────────────────────────────────────
            yield await _sse_event({"agent": "greeks", "status": "running"})
            t0 = time.time()
            greeks = await asyncio.get_event_loop().run_in_executor(
                None, greeks_agent, chain, market, oi, technical
            )
            elapsed = time.time() - t0
            logger.set_timing("greeks", elapsed)
            logger.log("greeks", _safe_dict(greeks), step=5)
            results["greeks"] = _safe_dict(greeks)
            yield await _sse_event({"agent": "greeks", "status": "done", "data": _safe_dict(greeks), "elapsed": round(elapsed, 2)})

            # ── Strategy ─────────────────────────────────────────────────────
            yield await _sse_event({"agent": "strategy", "status": "running"})
            funds = OpenAlgoService().get_funds()
            t0 = time.time()
            strategy = await asyncio.get_event_loop().run_in_executor(
                None, strategy_agent, market, technical, oi, greeks, chain, funds
            )
            elapsed = time.time() - t0
            logger.set_timing("strategy", elapsed)
            logger.log("strategy", _safe_dict(strategy), step=6)
            results["strategy"] = _safe_dict(strategy)
            _pending_strategy[run_id] = strategy
            yield await _sse_event({"agent": "strategy", "status": "done", "data": _safe_dict(strategy), "elapsed": round(elapsed, 2)})

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
            logger.save_master()

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
