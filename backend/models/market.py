from pydantic import BaseModel
from typing import Optional


class MarketData(BaseModel):
    nifty_spot: float
    vix: float
    change_pct: float
    today_open: float
    today_high: float
    today_low: float
    prev_close: float
    is_mock: bool = False
    data_source: str = "openalgo"


class TechnicalData(BaseModel):
    rsi: float
    rsi_signal: str
    macd_line: float
    macd_signal_text: str
    sma_20: float
    sma_50: float
    trend: str
    atr: float
    bb_width: float
