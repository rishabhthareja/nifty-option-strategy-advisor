import numpy as np
import pandas as pd
from models.market import MarketData, TechnicalData
from services.openalgo_client import OpenAlgoService


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


def _macd(series: pd.Series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return round(float(tr.ewm(span=period, adjust=False).mean().iloc[-1]), 2)


def _bollinger_width(series: pd.Series, period: int = 20) -> float:
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    width = (upper - lower) / sma
    return round(float(width.iloc[-1]), 4)


def technical_agent(market_data: MarketData) -> TechnicalData:
    service = OpenAlgoService()
    df = service.get_ohlcv(days=90)

    close = df["close"]

    rsi = _rsi(close)
    macd_line, macd_sig, macd_hist = _macd(close)
    sma_20 = round(float(close.rolling(20).mean().iloc[-1]), 2)
    sma_50 = round(float(close.rolling(50).mean().iloc[-1]), 2)
    atr = _atr(df)
    bb_width = _bollinger_width(close)

    spot = market_data.nifty_spot

    if spot > sma_20 and spot > sma_50 and sma_20 > sma_50:
        trend = "UPTREND"
    elif spot < sma_20 and spot < sma_50 and sma_20 < sma_50:
        trend = "DOWNTREND"
    elif spot > sma_20 and spot > sma_50:
        trend = "MILD UPTREND"
    elif spot < sma_20 and spot < sma_50:
        trend = "MILD DOWNTREND"
    else:
        trend = "SIDEWAYS"

    if rsi > 70:
        rsi_signal = "OVERBOUGHT"
    elif rsi < 30:
        rsi_signal = "OVERSOLD"
    elif 55 < rsi <= 70:
        rsi_signal = "MILD BULLISH"
    elif 30 <= rsi < 45:
        rsi_signal = "MILD BEARISH"
    else:
        rsi_signal = "NEUTRAL"

    macd_signal_text = "BULLISH" if macd_hist > 0 else "BEARISH"

    return TechnicalData(
        rsi=rsi,
        rsi_signal=rsi_signal,
        macd_line=round(macd_line, 2),
        macd_signal_text=macd_signal_text,
        sma_20=sma_20,
        sma_50=sma_50,
        trend=trend,
        atr=atr,
        bb_width=bb_width,
    )
