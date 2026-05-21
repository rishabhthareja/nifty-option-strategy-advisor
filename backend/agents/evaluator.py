from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from models.market import MarketData, TechnicalData
from models.options import OIAnalysis, GreeksData
from models.strategy import StrategyRecommendation, EvaluationResult
from services.langsmith_logger import save_to_langsmith_dataset
from services.analysis_digest import build_evaluator_digest_block
from config import GEMINI_MODEL, GOOGLE_API_KEY, MIN_EVALUATOR_SCORE


EVALUATOR_PROMPT = """
You are an independent risk manager and options strategy evaluator.
Your job is to critically assess a strategy recommendation made by another analyst.

Scoring criteria (0-10):
- Signal alignment (0-3): How well do market signals support this strategy?
- Risk/reward (0-2): Is the risk/reward ratio acceptable?
- Timing (0-2): Is this the right time given VIX, IV Rank, and trend?
- Strike selection (0-2): Are the strikes appropriately placed relative to support/resistance?
- Completeness (0-1): Is the reasoning clear and complete?

Set is_validated = True ONLY if quality_score >= {min_score}.

If the FACT DIGEST shows TRADE_READY false but the strategist recommended something other than WAIT,
cap the score accordingly and reject validation.

Be constructively critical. Identify genuine gaps or risks the strategy agent may have missed.

IRON_CONDOR requires different short put and short call strikes; IRON_BUTTERFLY requires the same short strike on both sides.
If the analyst mislabels structure or ignores 0 DTE / expiry-day gamma in the FACT DIGEST, penalize the score.
"""


def evaluator_agent(
    market: MarketData,
    technical: TechnicalData,
    oi: OIAnalysis,
    greeks: GreeksData,
    strategy: StrategyRecommendation,
    run_id: str,
    chain=None,
) -> EvaluationResult:
    llm = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=0.2,
    )

    structured_llm = llm.with_structured_output(EvaluationResult)

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            EVALUATOR_PROMPT.format(min_score=MIN_EVALUATOR_SCORE),
        ),
        (
            "human",
            """Evaluate the following options strategy recommendation:

FACT DIGEST (cross-check the analyst against this ground truth):
{digest_block}

MARKET CONDITIONS:
- Nifty Spot: {spot} | VIX: {vix} | Trend: {trend}
- RSI: {rsi} ({rsi_signal}) | MACD: {macd_signal}
- VIX Percentile (IV Rank): {iv_rank}% — {iv_env} (method: {iv_rank_method})
- Support: {support} | Resistance: {resistance} | PCR: {pcr} ({pcr_sentiment})
- ATM IV: {atm_iv}% | Expected Daily Move: ±{edm} pts

RECOMMENDED STRATEGY: {strategy_name} (Confidence: {confidence})
- Integrity: blocked={blocked} trade_ready={trade_ready} notes={integ_note}
- Sell Put: {sell_put} @ {sell_put_prem}
- Buy Put: {buy_put} @ {buy_put_prem}
- Sell Call: {sell_call} @ {sell_call_prem}
- Buy Call: {buy_call} @ {buy_call_prem}
- Net Premium (LTP-based approx): {net_prem} | Conservative net (bid/ask): {cons_net}
- Max Profit approx: {max_profit} | Conservative max profit: {cons_mxp}
- Max Loss approx: {max_loss} | Conservative max loss: {cons_mxl}
- Breakevens approx: {lower_be} — {upper_be} | Conservative BE: {cons_lbe} — {cons_ube}

ANALYST REASONING: {reasoning}

ALIGNED SIGNALS: {aligned}
CONFLICTING SIGNALS: {conflicting}

Provide your independent evaluation with a quality score, evidence for and against,
any gaps in analysis, and whether you validate this trade.""",
        ),
    ])

    digest = build_evaluator_digest_block(market, technical, oi, greeks, chain)

    result = (prompt | structured_llm).invoke({
        "digest_block": digest,
        "spot": market.nifty_spot,
        "vix": market.vix,
        "trend": technical.trend,
        "rsi": technical.rsi,
        "rsi_signal": technical.rsi_signal,
        "macd_signal": technical.macd_signal_text,
        "iv_rank": oi.iv_rank,
        "iv_env": oi.iv_environment,
        "iv_rank_method": getattr(oi, "iv_rank_method", "unknown"),
        "support": oi.support,
        "resistance": oi.resistance,
        "pcr": oi.pcr,
        "pcr_sentiment": oi.pcr_sentiment,
        "atm_iv": greeks.atm_iv,
        "edm": greeks.expected_daily_move,
        "strategy_name": strategy.strategy,
        "confidence": strategy.confidence,
        "blocked": getattr(strategy, "integrity_blocked", False),
        "trade_ready": getattr(strategy, "data_trade_ready", False),
        "integ_note": getattr(strategy, "integrity_note", "") or "none",
        "sell_put": strategy.sell_put_strike or "N/A",
        "sell_put_prem": strategy.sell_put_premium or "N/A",
        "buy_put": strategy.buy_put_strike or "N/A",
        "buy_put_prem": strategy.buy_put_premium or "N/A",
        "sell_call": strategy.sell_call_strike or "N/A",
        "sell_call_prem": strategy.sell_call_premium or "N/A",
        "buy_call": strategy.buy_call_strike or "N/A",
        "buy_call_prem": strategy.buy_call_premium or "N/A",
        "net_prem": strategy.net_premium or "N/A",
        "cons_net": getattr(strategy, "conservative_net_premium", None) or "N/A",
        "max_profit": strategy.max_profit or "N/A",
        "cons_mxp": getattr(strategy, "conservative_max_profit", None) or "N/A",
        "max_loss": strategy.max_loss or "N/A",
        "cons_mxl": (
            "Unlimited"
            if strategy.strategy == "SHORT_STRANGLE" and strategy.conservative_max_loss is None
            else (strategy.conservative_max_loss if strategy.conservative_max_loss is not None else "N/A")
        ),
        "lower_be": strategy.lower_breakeven or "N/A",
        "upper_be": strategy.upper_breakeven or "N/A",
        "cons_lbe": getattr(strategy, "conservative_lower_breakeven", None) or "N/A",
        "cons_ube": getattr(strategy, "conservative_upper_breakeven", None) or "N/A",
        "reasoning": strategy.reasoning,
        "aligned": ", ".join(strategy.aligned_signals) or "None",
        "conflicting": ", ".join(strategy.conflicting_signals) or "None",
    })

    # Enforce is_validated based on score
    result.is_validated = result.quality_score >= MIN_EVALUATOR_SCORE

    blocked = getattr(strategy, "integrity_blocked", False)
    td_ready = getattr(strategy, "data_trade_ready", True)
    if blocked or (strategy.strategy != "WAIT" and not td_ready):
        result.is_validated = False

    # Save to LangSmith dataset
    save_to_langsmith_dataset(
        dataset_name="nifty-options-evaluations",
        inputs={
            "market": market.model_dump(),
            "technical": technical.model_dump(),
            "oi": oi.model_dump(),
            "greeks": greeks.model_dump(),
            "strategy": strategy.model_dump(),
        },
        outputs=result.model_dump(),
        run_id=run_id,
    )

    return result
