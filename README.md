# Nifty Options Strategy Advisor

An AI-powered options strategy recommendation system for Nifty using a FastAPI backend and React frontend.

## Architecture

```
7-Agent Pipeline:
Market Data → Technical → Options Chain → OI Analysis → Greeks → Strategy (Gemini) → Evaluator (Gemini)
```

All agents run sequentially and stream progress to the frontend via SSE. The Strategy and Evaluator agents use Gemini 1.5 Flash via LangChain with structured output. Human-in-the-loop approval is required before any orders are placed.

## Prerequisites

- Python 3.10+
- Node.js 18+
- [OpenAlgo](https://github.com/marketcalls/openalgo) running on `http://127.0.0.1:5000` (optional — falls back to mock data)
- Google Gemini API key
- LangSmith API key (optional — for tracing)

## Backend Setup

```bash
cd nifty-options-advisor/backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
copy .env.example .env
# Edit .env with your API keys

# Run backend
uvicorn main:app --reload --port 8000
```

## Frontend Setup

```bash
cd nifty-options-advisor/frontend

npm install
npm run dev
```

Frontend runs at http://localhost:5173

## Environment Variables

| Variable | Description | Required |
|---|---|---|
| `OPENALGO_API_KEY` | OpenAlgo API key | No (uses mock data) |
| `OPENALGO_HOST` | OpenAlgo server URL | No |
| `GOOGLE_API_KEY` | Gemini API key | **Yes** |
| `LANGCHAIN_API_KEY` | LangSmith API key | No |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing | No |
| `LANGCHAIN_PROJECT` | LangSmith project name | No |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | System status and margin check |
| GET | `/live-data` | Current Nifty spot + VIX (fast) |
| GET | `/analyse/stream` | SSE stream of all 7 agent outputs |
| POST | `/approve` | Submit human approval/rejection |
| POST | `/execute` | Place orders (requires prior approval) |
| GET | `/history` | List past analysis runs from logs/ |
| POST | `/trade/open` | Open paper or live trade (entry snapshot) |
| GET | `/trade/list` | List trades + stats |
| GET | `/trade/{trade_id}` | Trade detail + daily marks |
| POST | `/trade/{trade_id}/mark` | Daily mark-to-market (hybrid broker/manual) |
| POST | `/trade/mark-all` | Auto-mark all open trades for today |
| POST | `/trade/{trade_id}/close` | Close trade and record realized P&L |
| GET | `/trade/stats` | Win rate, expectancy, totals |
| GET | `/trade/reviews/pending` | Paper trades awaiting Sensibull/premium input |
| POST | `/trade/review-now` | Manual position review (`MORNING` / `MIDDAY` / `EOD`) |
| GET | `/trade/{trade_id}/reviews` | Review history for a trade |
| GET | `/trade/{trade_id}/reviews/latest` | Latest completed (or pending) review |
| GET | `/trade/{trade_id}/review-summary` | Entry vs latest review side-by-side |
| POST | `/trade/{trade_id}/reviews/pending/complete` | Complete PAPER pending review (Sensibull snapshot) |

Scheduled reviews (IST, weekdays 09:15–15:30): **10:30 MORNING**, **13:30 MIDDAY**, **15:00 EOD**.

## Strategies

| Strategy | Condition |
|---|---|
| IRON_CONDOR | Sideways market, IV Rank > 40, PCR neutral |
| BULL_PUT_SPREAD | Mild bullish trend, PCR > 1.1 |
| BEAR_CALL_SPREAD | Mild bearish trend, PCR < 0.9 |
| SHORT_STRANGLE | Very high IV Rank > 60, high conviction |
| WAIT | VIX < 12, IV Rank < 30, conflicting signals |

## Mock Data

When OpenAlgo is not available, realistic mock data is used:
- Nifty spot: 22347.50
- VIX: 14.82
- Options chain: generated with artificial support at ATM-350 and resistance at ATM+350
- 90 days of synthetic OHLCV for technical indicators

## Logs

Each analysis run saves JSON files to:
```
logs/YYYY-MM-DD/<run_id>/
  00_master.json       ← complete run summary + journal (trade_outcome, next_check_condition)
  01_market_data.json
  02_technical.json    ← includes bb_width_signal
  03_options_chain.json
  04_oi_analysis.json  ← spot_to_resistance_pts, range_position, etc.
  05_greeks.json       ← expected_move_pct, candidates_rejected_itm
  06_strategy.json     ← guard_fired, guard_detail, would_trade_at_spot
  07_evaluator.json
```

## Configuration (config.py)

| Setting | Default | Description |
|---|---|---|
| `LOT_SIZE` | 65 | Nifty lot size (NSE revision from Jan 2026; verify on broker) |
| `NUM_LOTS` | 1 | Number of lots to trade |
| `STRIKE_INTERVAL` | 50 | Strike price interval |
| `MIN_EVALUATOR_SCORE` | 6.0 | Minimum score to validate a strategy |
| `MIN_VIX` | 12 | Minimum VIX for premium selling |
| `MIN_IV_RANK` | 30 | Minimum IV Rank for premium selling |

## Disclaimer

This tool is for **educational purposes only**. Options trading involves substantial risk of loss. Always consult a qualified financial advisor before trading. Past performance does not guarantee future results.
