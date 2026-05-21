import pandas as pd
from models.market import MarketData
from models.options import OIAnalysis
from config import STRIKE_INTERVAL
from services.openalgo_client import OpenAlgoService
from services.market_metrics import compute_vix_percentile, iv_environment_from_rank


def oi_analysis_agent(chain: pd.DataFrame, market_data: MarketData) -> OIAnalysis:
    spot = market_data.nifty_spot
    vix = market_data.vix
    chain_rows = len(chain) if chain is not None and not chain.empty else 0
    scope_label = f"loaded_chain ({chain_rows} strikes)"

    puts_below = chain[chain["strike"] <= spot].copy()
    calls_above = chain[chain["strike"] >= spot].copy()

    if not puts_below.empty:
        support_row = puts_below.loc[puts_below["put_oi"].idxmax()]
        support = int(support_row["strike"])
        support_put_oi = float(support_row["put_oi"])
    else:
        support = int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL - 350)
        support_put_oi = 0.0

    if not calls_above.empty:
        resistance_row = calls_above.loc[calls_above["call_oi"].idxmax()]
        resistance = int(resistance_row["strike"])
        resistance_call_oi = float(resistance_row["call_oi"])
    else:
        resistance = int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL + 350)
        resistance_call_oi = 0.0

    total_put_oi = chain["put_oi"].sum()
    total_call_oi = chain["call_oi"].sum()
    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0

    if pcr > 1.3:
        pcr_sentiment = "BULLISH"
    elif pcr < 0.7:
        pcr_sentiment = "BEARISH"
    elif 0.9 <= pcr <= 1.1:
        pcr_sentiment = "NEUTRAL"
    elif pcr > 1.1:
        pcr_sentiment = "MILDLY BULLISH"
    else:
        pcr_sentiment = "MILDLY BEARISH"

    max_pain_loss = float("inf")
    max_pain = int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL)
    for _, row in chain.iterrows():
        k = row["strike"]
        call_loss = ((chain["strike"] - k).clip(lower=0) * chain["call_oi"]).sum()
        put_loss = ((k - chain["strike"]).clip(lower=0) * chain["put_oi"]).sum()
        total_loss = call_loss + put_loss
        if total_loss < max_pain_loss:
            max_pain_loss = total_loss
            max_pain = int(k)

    max_pain_distance = round(spot - max_pain, 2)

    service = OpenAlgoService()
    iv_rank, iv_rank_method = compute_vix_percentile(service, vix)
    iv_environment = iv_environment_from_rank(iv_rank)

    range_width = resistance - support
    range_width_pct = round(range_width / spot * 100, 2)

    top_puts = (
        chain[chain["strike"] <= spot]
        .nlargest(5, "put_oi")[["strike", "put_oi"]]
        .rename(columns={"put_oi": "oi"})
        .to_dict("records")
    )
    top_calls = (
        chain[chain["strike"] >= spot]
        .nlargest(5, "call_oi")[["strike", "call_oi"]]
        .rename(columns={"call_oi": "oi"})
        .to_dict("records")
    )

    return OIAnalysis(
        support=support,
        support_put_oi=support_put_oi,
        resistance=resistance,
        resistance_call_oi=resistance_call_oi,
        pcr=pcr,
        pcr_sentiment=pcr_sentiment,
        max_pain=max_pain,
        max_pain_distance=max_pain_distance,
        iv_rank=iv_rank,
        iv_environment=iv_environment,
        iv_rank_method=iv_rank_method,
        pcr_scope=scope_label,
        max_pain_scope=scope_label,
        range_width=float(range_width),
        range_width_pct=range_width_pct,
        top_put_strikes=[{"strike": int(r["strike"]), "oi": int(r["oi"])} for r in top_puts],
        top_call_strikes=[{"strike": int(r["strike"]), "oi": int(r["oi"])} for r in top_calls],
    )
