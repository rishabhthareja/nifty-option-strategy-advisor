# Entry Guardrails — Implementation Addendum

This document records **confirmed decisions** for the entry-pipeline hardening plan. Phase 1 is implemented; later phases follow the main gap analysis.

## Confirmed thresholds

| Parameter | Value | Notes |
|-----------|-------|--------|
| `MIN_IV_RANK` | 30 | Enforced in Python; not 40 (prompt aspirational text may differ) |
| `MIN_VIX` | 12 | Pre-flight + post-LLM |
| `MIN_SR_BUFFER_PTS` | 50 | Phase 2+ — reframed rule (see below) |
| `MARGIN_ESTIMATE_MULT` | 1.8 | Phase 2+ |
| `MARGIN_BUFFER_PCT` | 10.0 | Phase 2+ |
| Session window | 09:45–15:00 IST weekdays | Pre-flight + post-LLM (Phase 1: pre-flight only) |

## Pre-flight (Phase 1)

Before the strategy LLM runs, `_collect_hard_blockers()` may return WAIT when:

- Live quote and/or option chain unavailable
- `VIX < MIN_VIX`
- `IV Rank < MIN_IV_RANK`
- Chain `DTE < MIN_ENTRY_DTE`
- Past 0 DTE entry cutoff (14:30 IST on expiry day)
- Weekend or outside 09:45–15:00 IST

Effects:

- No strategy Gemini call
- `preflight_blocked = true`, `wait_reason` prefixed with `[Pre-flight]`
- Evaluator **mandatory skip** — SSE: `{"agent": "evaluator", "status": "skipped", "reason": "pre_flight"}`

## Post-LLM belt-and-suspenders (Phase 1)

After LLM + financial normalization:

- `_apply_vix_guard()` — hard WAIT
- `_apply_iv_rank_guard()` — hard WAIT

Existing guards unchanged: POP/R:R, trade-ready, DTE, expiry day, 0 DTE time cutoff.

## S/R distance guard (Phase 2+ — revised design)

Do **not** block shorts “within 50 pts of support” when candidates are built from OI walls.

Reframed soft rule:

- Short put: `sell_put_strike > oi.support + MIN_SR_BUFFER_PTS`
- Short call: `sell_call_strike < oi.resistance - MIN_SR_BUFFER_PTS`

Shorts must sit **inside** the range (above support, below resistance), not on the walls.

## Margin (Phase 2+)

- `estimated_margin = abs(conservative_max_loss or max_loss) × 1.8`
- `required = estimated × (1 + MARGIN_BUFFER_PCT/100)`
- Hard WAIT if live `available_margin < required`; skip when `is_mock`
- **No `NUM_LOTS` auto-scale in Phase 1** — margin checks configured `NUM_LOTS` only (default 1)

## Lot size

- Do not hardcode NSE lot size in code without broker verification.
- On first run per process: log warning if `_lot_size(chain) != config.LOT_SIZE`.
- Phase 2+: order legs in `main.py` should use chain lot × `NUM_LOTS`.

## Hard NO vs soft NO

| Type | Behavior |
|------|----------|
| Hard NO | `_override_to_wait()` — `integrity_blocked`, clears strikes |
| Soft NO | `conflicting_signals` + confidence downgrade — Phase 2+ |

## Evaluator

Skipping evaluator on pre-flight WAIT is **mandatory**, not optional. Frontend UI for skipped state is deferred; backend SSE is stable for future UI.

## Phase 2 (implemented)

Post-LLM, after financials and VIX/IV guards:

- **`apply_theta_guard`** — IC / iron fly only; soft warn if `theta_per_day < MIN_THETA_PER_DAY` (₹40); hard WAIT if &lt; 50% of floor
- **`apply_margin_guard`** — `estimated = abs(conservative_max_loss or max_loss) × 1.8`; required += 10% buffer; hard WAIT on live funds; skip if mock
- **`main.py`** — passes `get_funds()` into `strategy_agent`; HITL leg qty = `order_quantity(chain)` (chain lot × `NUM_LOTS`)
- **`services/chain_utils.py`** — `lot_size_from_chain`, `order_quantity`
- Model fields: `theta_per_day`, `available_margin`, `estimated_margin`, `margin_note`
- Evaluator skipped path: `evaluation = None` safe for order_details / complete SSE

## Phase 3 (implemented)

Soft quality guards (confidence HIGH → MEDIUM; do not force WAIT unless combined with hard guards):

- **`apply_net_delta_guard`** — IC / iron fly; `|net_delta| > MAX_NET_DELTA` (0.10)
- **`apply_sr_buffer_guard`** — short put must be **above** `support + MIN_SR_BUFFER_PTS`; short call **below** `resistance - buffer`
- **`apply_spread_width_guard`** — any leg with `ask - bid > MAX_BID_ASK_SPREAD` (₹10)

Model field: `net_delta`.

## Phase roadmap

| Phase | Scope |
|-------|--------|
| **1 (done)** | Gaps 1, 2, 6; session in pre-flight; evaluator skip; lot mismatch warning |
| **2 (done)** | Margin, theta, `main.py` lot qty fix |
| **3 (done)** | Net delta, S/R buffer, bid-ask spread |
| **4** | Session post-LLM guard (if not duplicated), lot consistency signal |
