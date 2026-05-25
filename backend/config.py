from dotenv import load_dotenv
import os

load_dotenv()

from ssl_setup import configure_ssl

configure_ssl()  # before Gemini / LangSmith HTTPS calls

OPENALGO_API_KEY = os.getenv("OPENALGO_API_KEY", "")
OPENALGO_HOST = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000")
OPENALGO_CALL_TIMEOUT_SECS = float(os.getenv("OPENALGO_CALL_TIMEOUT_SECS", "8"))
# Option chain + multiquote can take 30–60s on a wide NFO book
OPENALGO_CHAIN_TIMEOUT_SECS = float(os.getenv("OPENALGO_CHAIN_TIMEOUT_SECS", "90"))
LIVE_DATA_CACHE_SECS = float(os.getenv("LIVE_DATA_CACHE_SECS", "15"))
ENABLE_LIVE_ORDERS = os.getenv("ENABLE_LIVE_ORDERS", "false").lower() == "true"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

LANGCHAIN_TRACING_V2 = os.getenv("LANGCHAIN_TRACING_V2", "false")
LANGCHAIN_ENDPOINT = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY", "")
LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT", "nifty-options-advisor")

os.environ["LANGCHAIN_TRACING_V2"] = LANGCHAIN_TRACING_V2
os.environ["LANGCHAIN_ENDPOINT"] = LANGCHAIN_ENDPOINT
os.environ["LANGCHAIN_API_KEY"] = LANGCHAIN_API_KEY
os.environ["LANGCHAIN_PROJECT"] = LANGCHAIN_PROJECT

SYMBOL = "NIFTY"
EXCHANGE = "NFO"
LOT_SIZE = 65
NUM_LOTS = 1
STRIKE_INTERVAL = 50

MIN_EVALUATOR_SCORE = 6.0
MIN_VIX = 12
MIN_IV_RANK = 30  # Enforced in Python pre-flight + post-LLM guards (not prompt-only)

# New-entry session window (IST, weekdays)
SESSION_OPEN_HOUR = 9
SESSION_OPEN_MINUTE = 45
SESSION_CLOSE_HOUR = 15
SESSION_CLOSE_MINUTE = 0

# Phase 2: margin + theta guards (post-LLM, after financials)
MARGIN_ESTIMATE_MULT = 1.8
MARGIN_BUFFER_PCT = 10.0
RISK_FREE_RATE = 0.07  # annualized, for log-normal POP (Indian T-bill approx)

MIN_THETA_PER_DAY = 40.0  # ₹ minimum daily theta for NUM_LOTS at chain lot size

# Phase 3: structure quality (soft warnings unless noted)
MAX_NET_DELTA = 0.10
MIN_SR_BUFFER_PTS = 50  # short put above support+buffer; short call below resistance-buffer
MAX_BID_ASK_SPREAD = 10.0  # ₹ per leg

# Option chain width (strikes each side of ATM from broker).
# OpenAlgo fills OI via multiquotes; most brokers cap ~100 symbols (2 legs × (2×N+1) strikes).
# N=25 → 102 symbols → OI comes back as 0. Max reliable N is 24 (98 symbols).
CHAIN_STRIKE_COUNT = 24
OPENALGO_MAX_STRIKE_COUNT = 24

# Block new short-premium on 0 DTE after this IST time
ENTRY_CUTOFF_HOUR = 14
ENTRY_CUTOFF_MINUTE = 30

# Weekly-style new entries: load chain and allow trades only when DTE >= MIN_ENTRY_DTE
MIN_ENTRY_DTE = 4
PREFERRED_DTE_MIN = 5
PREFERRED_DTE_MAX = 8

# Short-leg delta band for candidate B (iron condor)
SHORT_DELTA_TARGET = 0.22
SHORT_DELTA_MIN = 0.18
SHORT_DELTA_MAX = 0.28

# Phase 1C: POP / reward-risk scoring on strike candidates
POP_DELTA_TARGET = 0.17
POP_DELTA_MIN = 0.14
POP_DELTA_MAX = 0.20
MIN_SHORT_MOVE_MULT = 0.75  # shorts should be at least this × implied move from spot
MIN_EST_POP_PCT = 52.0
MIN_REWARD_RISK = 0.55
POP_SCORE_WEIGHT = 0.6
RR_SCORE_WEIGHT = 0.4
