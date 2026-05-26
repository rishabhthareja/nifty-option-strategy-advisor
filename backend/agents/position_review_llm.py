"""Gemini reasoning for position reviews — explains pre-determined actions only."""

from __future__ import annotations

import json

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from config import GEMINI_MODEL, GOOGLE_API_KEY
from models.review import PositionReview
from models.trade import TradeRecord


CORRELATED_PROMPT = """
You are a Nifty weekly options risk manager. The system has ALREADY decided the action.
Do NOT change or second-guess the action. Explain it in 2-3 sentences using the numbers below.

Pre-determined decision:
- action_category: {action_category}
- confidence: {action_confidence}
- exit_signal: {exit_signal}
- recommended_action: {recommended_action}

Position:
- Strategy: {strategy} | DTE remaining: {dte}
- Strikes: sell put {sell_put} / buy put {buy_put} | sell call {sell_call} / buy call {buy_call}

Move context:
- move_class: {move_class} | move_vs_expected: {move_vs_expected} | daily_expected_move: {daily_expected_move}
- spot today: {spot}

Greeks / OI:
- short_call_delta today: {sc_delta} | short_put_delta today: {sp_delta}
- net_delta today: {net_delta} | net_theta today: {net_theta}
- theta_delta_ratio: {theta_ratio} | theta_compensating: {theta_compensating}
- call OI class: {oi_call_class} (conf {oi_call_conf}) | put OI class: {oi_put_class} (conf {oi_put_conf})
- pending_greeks: {pending_greeks}

P&L:
- unrealized_pnl: {unrealized_pnl} | pnl_pct_of_max_profit: {pnl_pct}

Evidence codes: {evidence_list}

Write plain English for the trader. Cite specific numbers. If action is HOLD, say why holding is appropriate.
If WATCH_CLOSELY or side-close, say what to monitor. Never invent a different action.
"""


SOFT_LEGACY_PROMPT = """
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


def _invoke_llm(prompt_template: str, variables: dict) -> str:
    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0.3,
    )
    prompt = ChatPromptTemplate.from_template(prompt_template)
    chain = prompt | llm
    msg = chain.invoke(variables)
    content = getattr(msg, "content", str(msg))
    if isinstance(content, list):
        parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
        return " ".join(parts).strip()[:800]
    return str(content).strip()[:800]


def generate_correlated_reasoning(
    trade: TradeRecord, review: PositionReview
) -> str:
    evidence = review.evidence_list or []
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = [evidence]
    return _invoke_llm(
        CORRELATED_PROMPT,
        {
            "action_category": review.action_category or "HOLD",
            "action_confidence": review.action_confidence or "LOW",
            "exit_signal": review.exit_signal or "HOLD",
            "recommended_action": review.recommended_action or "HOLD",
            "strategy": trade.strategy,
            "dte": review.dte_remaining,
            "sell_put": trade.sell_put_strike,
            "buy_put": trade.buy_put_strike,
            "sell_call": trade.sell_call_strike,
            "buy_call": trade.buy_call_strike,
            "move_class": review.move_class,
            "move_vs_expected": review.move_vs_expected,
            "daily_expected_move": review.daily_expected_move,
            "spot": review.current_spot,
            "sc_delta": review.short_call_delta_today,
            "sp_delta": review.short_put_delta_today,
            "net_delta": review.net_delta_today,
            "net_theta": review.net_theta_today,
            "theta_ratio": review.theta_delta_ratio,
            "theta_compensating": review.theta_compensating,
            "oi_call_class": review.oi_call_classification,
            "oi_call_conf": review.oi_call_confidence,
            "oi_put_class": review.oi_put_classification,
            "oi_put_conf": review.oi_put_confidence,
            "pending_greeks": review.pending_greeks,
            "unrealized_pnl": review.unrealized_pnl,
            "pnl_pct": review.pnl_pct_of_max_profit,
            "evidence_list": ", ".join(evidence) if evidence else "none",
        },
    )


def generate_soft_exit_reasoning(
    trade: TradeRecord, review: PositionReview
) -> str:
    return _invoke_llm(
        SOFT_LEGACY_PROMPT,
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
        },
    )
