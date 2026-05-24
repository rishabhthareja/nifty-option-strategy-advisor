"""Quick live check for Phase 1A weekly expiry (requires OpenAlgo on OPENALGO_HOST)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MIN_ENTRY_DTE, PREFERRED_DTE_MIN, PREFERRED_DTE_MAX
from services.expiry_utils import (
    days_to_expiry,
    ist_now,
    nifty_weekly_expiry_for_session,
    resolve_weekly_entry_expiry,
    resolve_chain_expiry,
    is_below_min_entry_dte,
)
from services.openalgo_client import OpenAlgoService


def main() -> int:
    now = ist_now()
    session = nifty_weekly_expiry_for_session(now)
    target = resolve_weekly_entry_expiry(now)
    print(f"IST now: {now.isoformat()}")
    print(f"MIN_ENTRY_DTE={MIN_ENTRY_DTE} preferred={PREFERRED_DTE_MIN}-{PREFERRED_DTE_MAX}")
    print(f"Session calendar expiry: {session} (DTE {days_to_expiry(session, now)})")
    print(f"Weekly entry target:   {target} (DTE {days_to_expiry(target, now)})")

    svc = OpenAlgoService()
    resolved = resolve_chain_expiry(svc, now)
    dte = days_to_expiry(resolved, now)
    print(f"Resolved chain expiry: {resolved} (DTE {dte})")
    if is_below_min_entry_dte(resolved, now):
        print("WARN: resolved expiry below MIN_ENTRY_DTE — strategy guard should WAIT")
        return 1
    print("OK: chain expiry meets weekly entry minimum")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
