import math
import pandas as pd
from models.market import MarketData
from models.options import OIAnalysis, GreeksData, StrikeGreeks
from config import STRIKE_INTERVAL
from services.market_metrics import straddle_expected_move


def _get_strike_greeks(chain: pd.DataFrame, strike: int, option_type: str) -> StrikeGreeks:
    row = chain[chain["strike"] == strike]
    if row.empty:
        nearest = chain.iloc[(chain["strike"] - strike).abs().argsort()[:1]]
        row = nearest

    r = row.iloc[0]
    prefix = "put" if option_type == "put" else "call"

    return StrikeGreeks(
        strike=int(r["strike"]),
        ltp=round(float(r[f"{prefix}_ltp"]), 2),
        price_used=round(float(r.get(f"{prefix}_price_used", r[f"{prefix}_ltp"])), 2),
        price_source=str(r.get(f"{prefix}_price_source", "ltp") or "ltp"),
        delta=round(float(r[f"{prefix}_delta"]), 4),
        gamma=round(float(r[f"{prefix}_gamma"]), 6),
        theta=round(float(r[f"{prefix}_theta"]), 4),
        vega=round(float(r[f"{prefix}_vega"]), 4),
        iv=round(float(r[f"{prefix}_iv"]), 2),
        greeks_source=str(r.get(f"{prefix}_greeks_source", "calculated_ltp") or "calculated_ltp"),
    )


def greeks_agent(chain: pd.DataFrame, market_data: MarketData, oi_analysis: OIAnalysis) -> GreeksData:
    spot = market_data.nifty_spot
    atm_strike = int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL)

    atm_row = chain[chain["strike"] == atm_strike]
    if atm_row.empty:
        atm_row = chain.iloc[(chain["strike"] - atm_strike).abs().argsort()[:1]]

    atm_iv = round(
        (float(atm_row.iloc[0]["call_iv"]) + float(atm_row.iloc[0]["put_iv"])) / 2, 2
    )

    straddle_move, straddle_price, move_method = straddle_expected_move(chain, atm_strike, spot)
    if move_method == "atm_straddle_ltp" and straddle_move > 0:
        expected_daily_move = straddle_move
        expected_move_method = move_method
    else:
        expected_daily_move = round(spot * (atm_iv / 100) / math.sqrt(365), 2)
        expected_move_method = "iv_formula_fallback"
        straddle_price = 0.0

    sell_put_strike = oi_analysis.support + STRIKE_INTERVAL
    buy_put_strike = oi_analysis.support - STRIKE_INTERVAL
    sell_call_strike = oi_analysis.resistance - STRIKE_INTERVAL
    buy_call_strike = oi_analysis.resistance + STRIKE_INTERVAL

    atm_call = _get_strike_greeks(chain, atm_strike, "call")
    atm_put = _get_strike_greeks(chain, atm_strike, "put")
    sell_put = _get_strike_greeks(chain, sell_put_strike, "put")
    buy_put = _get_strike_greeks(chain, buy_put_strike, "put")
    sell_call = _get_strike_greeks(chain, sell_call_strike, "call")
    buy_call = _get_strike_greeks(chain, buy_call_strike, "call")
    source_counts = {
        sell_put.greeks_source,
        buy_put.greeks_source,
        sell_call.greeks_source,
        buy_call.greeks_source,
    }

    return GreeksData(
        atm_strike=atm_strike,
        atm_iv=atm_iv,
        expected_daily_move=expected_daily_move,
        expected_move_method=expected_move_method,
        straddle_price=straddle_price,
        sell_put_strike=sell_put_strike,
        buy_put_strike=buy_put_strike,
        sell_call_strike=sell_call_strike,
        buy_call_strike=buy_call_strike,
        sell_put=sell_put,
        buy_put=buy_put,
        sell_call=sell_call,
        buy_call=buy_call,
        atm_call=atm_call,
        atm_put=atm_put,
        greeks_source=source_counts.pop() if len(source_counts) == 1 else "mixed",
    )
