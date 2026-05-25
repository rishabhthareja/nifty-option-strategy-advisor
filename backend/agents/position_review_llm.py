"""Gemini reasoning for SOFT_EXIT position reviews (MIDDAY / EOD only)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from config import GEMINI_MODEL, GOOGLE_API_KEY
from models.review import PositionReview
from models.trade import TradeRecord


REVIEW_PROMPT = """
You are a Nifty weekly options risk manager reviewing an open iron condor (or credit spread).

Write plain English reasoning (max 3 sentences) explaining why the fired soft exit signals
suggest closing now versus holding. Be actionable, not academic.

Strategy: {strategy}
Strikes: sell put {sell_put} / buy put {buy_put} | sell call {sell_call} / buy call {buy_call}
Entry date: {entry_date} | DTE remaining: {dte}

Entry snapshot:
- IV Rank: {iv_rank_entry} | RSI: {rsi_entry} | BB width: {bb_width_entry} | PCR: {pcr_entry}
- Cushion to short call (entry): {cushion_call_entry} pts | short put: {cushion_put_entry} pts

Today:
- Spot: {spot} | IV Rank: {iv_rank_today} | RSI: {rsi_today} | BB width: {bb_width_today} | PCR: {pcr_today}
- Cushion call: {cushion_call_today} | cushion put: {cushion_put_today}
- Unrealized P&L: ₹{unrealized_pnl} ({pnl_pct}% of max profit)
- Call OI change: {call_oi_change}% ({call_oi_signal}) | Put OI change: {put_oi_change}%
- Short call delta today: {sc_delta} | short put delta: {sp_delta}
- Net theta today: {net_theta_today} (entry {net_theta_entry})

Soft exit codes fired: {codes}

Explain why this combination warrants closing or careful review now.
"""


def generate_soft_exit_reasoning(
    trade: TradeRecord, review: PositionReview
) -> str:
    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0.3,
    )
    prompt = ChatPromptTemplate.from_template(REVIEW_PROMPT)
    chain = prompt | llm
    msg = chain.invoke(
        {
            "strategy": trade.strategy,
            "sell_put": trade.sell_put_strike,
            "buy_put": trade.buy_put_strike,
            "sell_call": trade.sell_call_strike,
            "buy_call": trade.buy_call_strike,
            "entry_date": trade.entry_date,
            "dte": review.dte_remaining,
            "iv_rank_entry": trade.iv_rank_at_entry,
            "rsi_entry": trade.rsi_at_entry,
            "bb_width_entry": trade.bb_width_at_entry,
            "pcr_entry": trade.pcr_at_entry,
            "cushion_call_entry": review.cushion_call_entry,
            "cushion_put_entry": review.cushion_put_entry,
            "spot": review.current_spot,
            "iv_rank_today": review.iv_rank_today,
            "rsi_today": review.rsi_today,
            "bb_width_today": review.bb_width_today,
            "pcr_today": review.pcr_today,
            "cushion_call_today": review.cushion_call_today,
            "cushion_put_today": review.cushion_put_today,
            "unrealized_pnl": review.unrealized_pnl,
            "pnl_pct": review.pnl_pct_of_max_profit,
            "call_oi_change": review.call_oi_change_pct,
            "call_oi_signal": review.call_oi_signal,
            "put_oi_change": review.put_oi_change_pct,
            "sc_delta": review.short_call_delta_today,
            "sp_delta": review.short_put_delta_today,
            "net_theta_today": review.net_theta_today,
            "net_theta_entry": review.net_theta_entry,
            "codes": ", ".join(review.exit_reason_codes or []),
        }
    )
    content = getattr(msg, "content", str(msg))
    if isinstance(content, list):
        parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
        return " ".join(parts).strip()[:800]
    return str(content).strip()[:800]
