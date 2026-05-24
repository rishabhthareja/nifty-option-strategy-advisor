import math
import random
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date, datetime, timedelta
import pandas as pd
import numpy as np
from config import (
    LOT_SIZE,
    OPENALGO_API_KEY,
    OPENALGO_HOST,
    OPENALGO_CALL_TIMEOUT_SECS,
    STRIKE_INTERVAL,
    CHAIN_STRIKE_COUNT,
    OPENALGO_MAX_STRIKE_COUNT,
)


def _call_with_timeout(func, timeout_secs: float):
    """Run a blocking OpenAlgo SDK call without hanging the API worker indefinitely."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(func)
        try:
            return future.result(timeout=timeout_secs)
        except FuturesTimeoutError as exc:
            raise TimeoutError(f"OpenAlgo call timed out after {timeout_secs}s") from exc


RISK_FREE_RATE = 0.06


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _bs_price(spot: float, strike: float, time_years: float, rate: float, vol: float, option_type: str) -> float:
    if time_years <= 0 or vol <= 0:
        return max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)

    sqrt_t = math.sqrt(time_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * time_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t

    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * time_years) * _norm_cdf(d2)
    return strike * math.exp(-rate * time_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _implied_volatility(price: float, spot: float, strike: float, time_years: float, option_type: str) -> float:
    intrinsic = max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    if price <= intrinsic or spot <= 0 or strike <= 0 or time_years <= 0:
        return 0.0

    low = 0.01
    high = 3.0
    for _ in range(80):
        mid = (low + high) / 2
        model_price = _bs_price(spot, strike, time_years, RISK_FREE_RATE, mid, option_type)
        if model_price > price:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def _black_scholes_greeks(
    price: float,
    spot: float,
    strike: float,
    time_years: float,
    option_type: str,
) -> dict:
    vol = _implied_volatility(price, spot, strike, time_years, option_type)
    if vol <= 0 or time_years <= 0:
        return {"iv": 0.0, "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    sqrt_t = math.sqrt(time_years)
    d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * vol * vol) * time_years) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (
            -(spot * _norm_pdf(d1) * vol) / (2 * sqrt_t)
            - RISK_FREE_RATE * strike * math.exp(-RISK_FREE_RATE * time_years) * _norm_cdf(d2)
        ) / 365
    else:
        delta = _norm_cdf(d1) - 1
        theta = (
            -(spot * _norm_pdf(d1) * vol) / (2 * sqrt_t)
            + RISK_FREE_RATE * strike * math.exp(-RISK_FREE_RATE * time_years) * _norm_cdf(-d2)
        ) / 365

    gamma = _norm_pdf(d1) / (spot * vol * sqrt_t)
    vega = spot * _norm_pdf(d1) * sqrt_t / 100

    return {
        "iv": round(vol * 100, 2),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
    }


def _generate_mock_chain(spot: float) -> pd.DataFrame:
    """Generate a realistic mock options chain centred on spot."""
    atm = round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL
    strikes = [atm + i * STRIKE_INTERVAL for i in range(-7, 8)]

    rows = []
    base_iv = 14.5

    for strike in strikes:
        moneyness = (strike - spot) / spot
        distance = abs(moneyness)

        iv_call = base_iv + distance * 80 + random.uniform(-0.5, 0.5)
        iv_put = base_iv + distance * 80 + random.uniform(-0.5, 0.5)

        d1_call = (math.log(spot / strike) + (0.07 + 0.5 * (iv_call / 100) ** 2) * 0.019) / (
            (iv_call / 100) * math.sqrt(0.019)
        )

        def norm_cdf(x):
            return 0.5 * (1 + math.erf(x / math.sqrt(2)))

        delta_call = norm_cdf(d1_call)
        delta_put = delta_call - 1

        # Generate OI: highest at ATM-350 for puts, ATM+350 for calls
        support_strike = atm - 350
        resistance_strike = atm + 350

        put_oi_base = max(0, 5_00_000 - abs(strike - support_strike) * 1200)
        put_oi = int(put_oi_base + random.uniform(-20000, 20000))

        call_oi_base = max(0, 5_00_000 - abs(strike - resistance_strike) * 1200)
        call_oi = int(call_oi_base + random.uniform(-20000, 20000))

        t = 0.019  # ~7 days
        r = 0.07
        sigma_c = iv_call / 100
        sigma_p = iv_put / 100

        def bs_call_price(S, K, T, r, sigma):
            if T <= 0:
                return max(S - K, 0)
            d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)
            return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)

        call_ltp = round(bs_call_price(spot, strike, t, r, sigma_c) + random.uniform(-1, 1), 2)
        put_ltp = round(max(call_ltp - spot + strike * math.exp(-r * t), 0.5) + random.uniform(-1, 1), 2)

        gamma = norm_cdf(d1_call) / (spot * sigma_c * math.sqrt(t)) * 0.001
        theta_call = -(spot * sigma_c * norm_cdf(d1_call)) / (2 * math.sqrt(t)) / 365
        vega = spot * math.sqrt(t) * norm_cdf(d1_call) / 100

        rows.append({
            "strike": int(strike),
            "lot_size": LOT_SIZE,
            "put_oi": max(put_oi, 0),
            "call_oi": max(call_oi, 0),
            "put_ltp": max(round(put_ltp, 2), 0.5),
            "call_ltp": max(round(call_ltp, 2), 0.5),
            "put_bid": max(round(put_ltp - 0.1, 2), 0.05),
            "put_ask": max(round(put_ltp + 0.1, 2), 0.05),
            "call_bid": max(round(call_ltp - 0.1, 2), 0.05),
            "call_ask": max(round(call_ltp + 0.1, 2), 0.05),
            "put_price_used": max(round(put_ltp, 2), 0.5),
            "call_price_used": max(round(call_ltp, 2), 0.5),
            "put_price_source": "mock",
            "call_price_source": "mock",
            "put_greeks_source": "mock",
            "call_greeks_source": "mock",
            "put_iv": round(iv_put, 2),
            "call_iv": round(iv_call, 2),
            "put_delta": round(delta_put, 4),
            "call_delta": round(delta_call, 4),
            "put_theta": round(theta_call * 1.05, 4),
            "call_theta": round(theta_call, 4),
            "put_vega": round(vega * 0.98, 4),
            "call_vega": round(vega, 4),
            "put_gamma": round(gamma * 0.98, 6),
            "call_gamma": round(gamma, 6),
        })

    return pd.DataFrame(rows)


class OpenAlgoService:
    def __init__(self):
        self._client = None
        self._mock_spot = 22347.50
        self._mock_vix = 14.82
        self._connected = False
        self._try_connect()

    def _try_connect(self):
        try:
            from openalgo import api
            self._client = api(api_key=OPENALGO_API_KEY, host=OPENALGO_HOST)
            self._connected = True
        except Exception:
            self._connected = False

    @staticmethod
    def _response_data(response):
        if isinstance(response, dict) and response.get("status") == "success":
            return response.get("data", {})
        return {}

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _price_with_source(self, leg: dict) -> tuple[float, str]:
        bid = self._to_float(leg.get("bid"))
        ask = self._to_float(leg.get("ask"))
        if bid > 0 and ask > 0 and ask >= bid:
            return round((bid + ask) / 2, 2), "bid_ask_mid"

        ltp = self._to_float(leg.get("ltp"))
        if ltp > 0:
            return ltp, "ltp"

        return 0.0, "unavailable"

    _OI_KEYS = (
        "oi",
        "open_interest",
        "openinterest",
        "openInterest",
        "oi_qty",
        "oiQty",
        "total_oi",
        "totalOi",
        "net_oi",
        "netoi",
        "chngoi",
        "chng_oi",
    )

    def _extract_oi_from_mapping(self, obj: dict | None) -> float | None:
        """Return OI if a non-negative numeric value is found; else None."""
        if not isinstance(obj, dict):
            return None
        for key in self._OI_KEYS:
            if key not in obj:
                continue
            val = obj.get(key)
            if val is None or val == "":
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if fval >= 0:
                return fval
        return None

    def _extract_oi(self, leg: dict) -> float:
        """
        OpenAlgo / broker payloads vary. Read OI from flat leg or shallow nests
        (e.g. quote, market, data).
        """
        if not isinstance(leg, dict):
            return 0.0
        direct = self._extract_oi_from_mapping(leg)
        if direct is not None:
            return float(direct)
        for nest in ("quote", "quotes", "market", "market_data", "data", "info", "snapshot", "depth"):
            inner = leg.get(nest)
            if isinstance(inner, dict):
                nested = self._extract_oi_from_mapping(inner)
                if nested is not None:
                    return float(nested)
        return 0.0

    def _chain_total_oi(self, chain: list) -> float:
        total = 0.0
        for item in chain:
            if not isinstance(item, dict):
                continue
            ce = item.get("ce") or {}
            pe = item.get("pe") or {}
            if isinstance(ce, dict):
                total += self._extract_oi(ce)
            if isinstance(pe, dict):
                total += self._extract_oi(pe)
        return total

    @staticmethod
    def _cap_strike_count(strike_count: int) -> int:
        """Keep multiquote batch within broker limit (~100 option symbols)."""
        cap = max(1, int(OPENALGO_MAX_STRIKE_COUNT))
        return min(max(1, int(strike_count)), cap)

    def _merge_chain_oi(self, target_chain: list, oi_chain: list) -> list:
        """Copy OI from a smaller chain onto a wider chain (same strikes)."""
        by_strike: dict[float, dict] = {}
        for item in oi_chain:
            if not isinstance(item, dict):
                continue
            try:
                by_strike[float(item.get("strike", 0))] = item
            except (TypeError, ValueError):
                continue

        merged = []
        for item in target_chain:
            if not isinstance(item, dict):
                merged.append(item)
                continue
            strike_key = float(item.get("strike", 0))
            donor = by_strike.get(strike_key)
            row = dict(item)
            if donor:
                for side in ("ce", "pe"):
                    leg = row.get(side)
                    d_leg = donor.get(side) if isinstance(donor, dict) else None
                    if not isinstance(leg, dict) or not isinstance(d_leg, dict):
                        continue
                    oi_val = self._extract_oi(d_leg)
                    if oi_val > 0:
                        leg = dict(leg)
                        leg["oi"] = oi_val
                        row[side] = leg
            merged.append(row)
        return merged

    def _fetch_option_chain_for_exchange(
        self, expiry: str, strike_count: int, exchange: str
    ) -> tuple[float, list] | None:
        try:
            chain_response = self._client.optionchain(
                underlying="NIFTY",
                exchange=exchange,
                expiry_date=expiry,
                strike_count=strike_count,
            )
            chain = chain_response.get("chain")
            if chain is None:
                chain = self._response_data(chain_response).get("chain")
            if isinstance(chain, list) and chain:
                return self._chain_total_oi(chain), chain
        except Exception as e:
            print(f"[OpenAlgo] optionchain exchange={exchange} strike_count={strike_count} failed: {e}")
        return None

    def _pick_best_exchange_chain(self, expiry: str, strike_count: int) -> tuple[list | None, str, float]:
        """Returns (chain, exchange, total_oi). Prefer NFO on tie."""
        candidates: list[tuple[float, str, list]] = []
        for exchange in ("NFO", "NSE_INDEX"):
            result = self._fetch_option_chain_for_exchange(expiry, strike_count, exchange)
            if result:
                oi_sum, chain = result
                candidates.append((oi_sum, exchange, chain))
        if not candidates:
            return None, "NSE_INDEX", 0.0
        best_oi, best_ex, best_chain = max(candidates, key=lambda c: (c[0], c[1] == "NFO"))
        return best_chain, best_ex, best_oi

    def _fetch_option_chain_candidates(self, expiry: str, strike_count: int) -> tuple[list | None, str, str]:
        """
        Fetch NIFTY chain. Prefer NFO when it carries OI (derivatives book);
        NSE_INDEX sometimes returns quotes with OI stripped by broker/adapter.
        Returns (chain_list, exchange_used, expiry_used).
        """
        requested = max(1, int(strike_count))
        capped = self._cap_strike_count(requested)
        if capped < requested:
            print(
                f"[OpenAlgo] optionchain: strike_count {requested} capped to {capped} "
                f"(broker multiquote limit ~100 symbols; OI is zero above that)"
            )

        wide_chain, wide_ex, wide_oi = self._pick_best_exchange_chain(expiry, capped)
        if wide_chain is None:
            return None, "NSE_INDEX", expiry

        if wide_oi > 0:
            print(f"[OpenAlgo] optionchain: using exchange={wide_ex} (total OI across chain ~ {wide_oi:,.0f})")
            return wide_chain, wide_ex, expiry

        # OI missing at capped width — retry narrower ladders (rate limits, feed glitches).
        for fallback_sc in (20, 15, 10, 5):
            if fallback_sc >= capped:
                continue
            narrow_chain, narrow_ex, narrow_oi = self._pick_best_exchange_chain(expiry, fallback_sc)
            if not narrow_chain or narrow_oi <= 0:
                continue
            if len(wide_chain) > len(narrow_chain):
                merged = self._merge_chain_oi(wide_chain, narrow_chain)
                print(
                    f"[OpenAlgo] optionchain: merged OI from strike_count={fallback_sc} into "
                    f"{capped}-wide chain via {narrow_ex} (total OI ~ {self._chain_total_oi(merged):,.0f})"
                )
                return merged, narrow_ex, expiry
            print(
                f"[OpenAlgo] optionchain: using exchange={narrow_ex} at strike_count={fallback_sc} "
                f"(total OI ~ {narrow_oi:,.0f})"
            )
            return narrow_chain, narrow_ex, expiry

        print(
            "[OpenAlgo] optionchain: OI is 0 for both NFO and NSE_INDEX — "
            "check broker feed / OpenAlgo version; quotes may still be valid."
        )
        return wide_chain, wide_ex, expiry

    @staticmethod
    def _compact_expiry(expiry: str | None) -> str | None:
        if not expiry:
            return None
        return expiry.replace("-", "").upper()

    @staticmethod
    def _time_to_expiry_years(expiry: str | None) -> float:
        if not expiry:
            return 1 / 365

        for fmt in ("%d%b%y", "%d%b%Y"):
            try:
                expiry_date = datetime.strptime(expiry.upper(), fmt)
                break
            except ValueError:
                expiry_date = None
        if expiry_date is None:
            return 1 / 365

        expiry_dt = expiry_date.replace(hour=15, minute=30)
        seconds = max((expiry_dt - datetime.now()).total_seconds(), 3600)
        return seconds / (365 * 24 * 60 * 60)

    def _next_available_expiry(self) -> str | None:
        try:
            response = self._client.expiry(symbol="NIFTY", exchange="NFO", instrumenttype="options")
            expiries = self._response_data(response)
            if isinstance(expiries, list) and expiries:
                return self._compact_expiry(expiries[0])
        except Exception as e:
            print(f"[OpenAlgo] expiry lookup failed: {e}")
        return None

    def _fetch_live_market_quotes(self) -> dict:
        quote = self._response_data(self._client.quotes(symbol="NIFTY", exchange="NSE_INDEX"))
        if not quote:
            raise ValueError("OpenAlgo NIFTY quote returned no data")

        vix_quote = self._response_data(self._client.quotes(symbol="INDIAVIX", exchange="NSE_INDEX"))
        ltp = self._to_float(quote.get("ltp"), self._mock_spot)
        prev_close = self._to_float(quote.get("prev_close"), ltp)
        vix = self._to_float(vix_quote.get("ltp"), self._mock_vix)
        return {
            "nifty_spot": ltp,
            "vix": vix,
            "change_pct": round((ltp - prev_close) / prev_close * 100, 2) if prev_close else 0.0,
            "today_open": self._to_float(quote.get("open"), ltp),
            "today_high": self._to_float(quote.get("high"), ltp),
            "today_low": self._to_float(quote.get("low"), ltp),
            "prev_close": prev_close,
            "is_mock": False,
            "data_source": "openalgo",
        }

    def get_market_data(self) -> dict:
        try:
            if self._connected and OPENALGO_API_KEY:
                return _call_with_timeout(
                    self._fetch_live_market_quotes,
                    OPENALGO_CALL_TIMEOUT_SECS,
                )
        except Exception as e:
            print(f"[OpenAlgo] get_market_data failed: {e}")

        spot = self._mock_spot
        prev_close = spot - 45.25
        return {
            "nifty_spot": spot,
            "vix": self._mock_vix,
            "change_pct": round((spot - prev_close) / prev_close * 100, 2),
            "today_open": 22301.20,
            "today_high": 22415.75,
            "today_low": 22280.50,
            "prev_close": prev_close,
            "is_mock": True,
            "data_source": "mock",
        }

    def get_ohlcv(self, days: int = 90) -> pd.DataFrame:
        """Return historical OHLCV for technical analysis."""
        try:
            if self._connected and OPENALGO_API_KEY:
                end_date = date.today()
                start_date = end_date - timedelta(days=max(days * 2, 120))
                hist = self._client.history(
                    symbol="NIFTY",
                    exchange="NSE_INDEX",
                    interval="D",
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                )
                if isinstance(hist, pd.DataFrame) and not hist.empty:
                    return hist.tail(days)
        except Exception as e:
            print(f"[OpenAlgo] get_ohlcv failed: {e}")

        # Generate mock OHLCV
        np.random.seed(42)
        dates = pd.date_range(end=pd.Timestamp.today(), periods=days, freq="B")
        closes = [22347.50]
        for _ in range(days - 1):
            closes.insert(0, closes[0] * (1 + np.random.normal(0, 0.008)))

        df = pd.DataFrame({"close": closes}, index=dates)
        df["open"] = df["close"].shift(1).fillna(df["close"])
        df["high"] = df["close"] + abs(np.random.normal(0, 80, days))
        df["low"] = df["close"] - abs(np.random.normal(0, 80, days))
        df["volume"] = np.random.randint(5_00_000, 15_00_000, days)
        return df[["open", "high", "low", "close", "volume"]]

    def get_vix_ohlcv(self, days: int = 252) -> pd.DataFrame:
        """Daily INDIAVIX history for percentile rank."""
        try:
            if self._connected and OPENALGO_API_KEY:
                end_date = date.today()
                start_date = end_date - timedelta(days=max(days * 2, 400))
                hist = self._client.history(
                    symbol="INDIAVIX",
                    exchange="NSE_INDEX",
                    interval="D",
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                )
                if isinstance(hist, pd.DataFrame) and not hist.empty:
                    return hist.tail(days)
        except Exception as e:
            print(f"[OpenAlgo] get_vix_ohlcv failed: {e}")

        np.random.seed(99)
        dates = pd.date_range(end=pd.Timestamp.today(), periods=days, freq="B")
        closes = list(14.0 + np.cumsum(np.random.normal(0, 0.15, days)))
        return pd.DataFrame({"close": closes}, index=dates)

    def get_options_chain(self, expiry: str = None) -> pd.DataFrame:
        try:
            if self._connected and OPENALGO_API_KEY:
                selected_expiry = self._compact_expiry(expiry) or self._next_available_expiry()
                if not selected_expiry:
                    raise ValueError("No OpenAlgo NIFTY option expiry available")

                market = self.get_market_data()
                spot = market["nifty_spot"]
                time_years = self._time_to_expiry_years(selected_expiry)

                chain, chain_exchange, expiry_used = self._fetch_option_chain_candidates(
                    selected_expiry, CHAIN_STRIKE_COUNT
                )
                if not chain and expiry:
                    fallback_expiry = self._next_available_expiry()
                    if fallback_expiry and fallback_expiry != selected_expiry:
                        selected_expiry = fallback_expiry
                        time_years = self._time_to_expiry_years(selected_expiry)
                        chain, chain_exchange, expiry_used = self._fetch_option_chain_candidates(
                            selected_expiry, CHAIN_STRIKE_COUNT
                        )

                if isinstance(chain, list) and chain:
                    rows = []
                    for item in chain:
                        ce = item.get("ce") or {}
                        pe = item.get("pe") or {}
                        strike = int(float(item.get("strike", 0)))
                        call_ltp = self._to_float(ce.get("ltp"))
                        put_ltp = self._to_float(pe.get("ltp"))
                        call_price, call_price_source = self._price_with_source(ce)
                        put_price, put_price_source = self._price_with_source(pe)
                        call_greeks = _black_scholes_greeks(call_price, spot, strike, time_years, "call")
                        put_greeks = _black_scholes_greeks(put_price, spot, strike, time_years, "put")
                        call_has_broker_greeks = any(ce.get(k) is not None for k in ("delta", "gamma", "theta", "vega", "iv"))
                        put_has_broker_greeks = any(pe.get(k) is not None for k in ("delta", "gamma", "theta", "vega", "iv"))
                        rows.append({
                            "strike": strike,
                            "lot_size": int(self._to_float(ce.get("lotsize") or pe.get("lotsize"), 65)),
                            "put_oi": self._extract_oi(pe),
                            "call_oi": self._extract_oi(ce),
                            "put_ltp": put_ltp,
                            "call_ltp": call_ltp,
                            "put_bid": self._to_float(pe.get("bid")),
                            "put_ask": self._to_float(pe.get("ask")),
                            "call_bid": self._to_float(ce.get("bid")),
                            "call_ask": self._to_float(ce.get("ask")),
                            "put_price_used": put_price,
                            "call_price_used": call_price,
                            "put_price_source": put_price_source,
                            "call_price_source": call_price_source,
                            "put_greeks_source": "broker" if put_has_broker_greeks else f"calculated_{put_price_source}",
                            "call_greeks_source": "broker" if call_has_broker_greeks else f"calculated_{call_price_source}",
                            "put_iv": self._to_float(pe.get("iv"), put_greeks["iv"]) or put_greeks["iv"],
                            "call_iv": self._to_float(ce.get("iv"), call_greeks["iv"]) or call_greeks["iv"],
                            "put_delta": self._to_float(pe.get("delta"), put_greeks["delta"]) or put_greeks["delta"],
                            "call_delta": self._to_float(ce.get("delta"), call_greeks["delta"]) or call_greeks["delta"],
                            "put_theta": self._to_float(pe.get("theta"), put_greeks["theta"]) or put_greeks["theta"],
                            "call_theta": self._to_float(ce.get("theta"), call_greeks["theta"]) or call_greeks["theta"],
                            "put_vega": self._to_float(pe.get("vega"), put_greeks["vega"]) or put_greeks["vega"],
                            "call_vega": self._to_float(ce.get("vega"), call_greeks["vega"]) or call_greeks["vega"],
                            "put_gamma": self._to_float(pe.get("gamma"), put_greeks["gamma"]) or put_greeks["gamma"],
                            "call_gamma": self._to_float(ce.get("gamma"), call_greeks["gamma"]) or call_greeks["gamma"],
                        })
                    df = pd.DataFrame(rows)
                    df.attrs["expiry"] = expiry_used
                    df.attrs["source"] = "openalgo"
                    df.attrs["chain_exchange"] = chain_exchange
                    return df
        except Exception as e:
            print(f"[OpenAlgo] get_options_chain failed: {e}")

        market = self.get_market_data()
        df = _generate_mock_chain(market["nifty_spot"])
        df.attrs["source"] = "mock"
        return df

    def place_order(self, leg: dict) -> dict:
        try:
            if self._connected and OPENALGO_API_KEY:
                result = self._client.placeorder(
                    strategy="NiftyOptionsAdvisor",
                    symbol=leg["symbol"],
                    action=leg["action"],
                    exchange="NFO",
                    price_type="MARKET",
                    product="NRML",
                    quantity=str(leg["quantity"]),
                )
                return {"status": "success", "result": result}
        except Exception as e:
            print(f"[OpenAlgo] place_order failed: {e}")

        return {"status": "mock", "symbol": leg["symbol"], "action": leg["action"]}

    def get_funds(self) -> dict:
        try:
            if self._connected and OPENALGO_API_KEY:
                return self._client.funds()
        except Exception as e:
            print(f"[OpenAlgo] get_funds failed: {e}")

        return {"available_margin": 2_00_000.0, "is_mock": True}
