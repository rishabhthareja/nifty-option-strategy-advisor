from dotenv import load_dotenv
import os

load_dotenv()

from ssl_setup import configure_ssl

configure_ssl()  # before Gemini / LangSmith HTTPS calls

OPENALGO_API_KEY = os.getenv("OPENALGO_API_KEY", "")
OPENALGO_HOST = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000")
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
MIN_IV_RANK = 30

# Option chain width (strikes each side of ATM from broker).
# OpenAlgo fills OI via multiquotes; most brokers cap ~100 symbols (2 legs × (2×N+1) strikes).
# N=25 → 102 symbols → OI comes back as 0. Max reliable N is 24 (98 symbols).
CHAIN_STRIKE_COUNT = 24
OPENALGO_MAX_STRIKE_COUNT = 24

# Block new short-premium on 0 DTE after this IST time
ENTRY_CUTOFF_HOUR = 14
ENTRY_CUTOFF_MINUTE = 30
