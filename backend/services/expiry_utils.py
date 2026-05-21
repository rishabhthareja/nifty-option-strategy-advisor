"""Nifty weekly expiry helpers (Tuesday weekly) and DTE for prompts/guardrails."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# Nifty index weekly options expire on Tuesday (post Sep 2024 regime).
NIFTY_WEEKLY_EXPIRY_WEEKDAY = 1  # Monday=0, Tuesday=1


def ist_now() -> datetime:
    return datetime.now(IST)


def compact_expiry(dt: date) -> str:
    """OpenAlgo-style compact expiry, e.g. 20MAY25."""
    return dt.strftime("%d%b%y").upper()


def parse_compact_expiry(expiry: str | None) -> date | None:
    if not expiry:
        return None
    raw = expiry.replace("-", "").strip().upper()
    for fmt in ("%d%b%y", "%d%b%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def days_to_expiry(expiry: str | None, now: datetime | None = None) -> int | None:
    exp_date = parse_compact_expiry(expiry)
    if exp_date is None:
        return None
    ref = (now or ist_now()).date()
    return (exp_date - ref).days


def is_expiry_day(expiry: str | None, now: datetime | None = None) -> bool:
    dte = days_to_expiry(expiry, now)
    return dte == 0


def nifty_weekly_expiry_on_or_after(from_date: date) -> date:
    """Next Nifty weekly expiry date (Tuesday) on or after from_date."""
    days_ahead = (NIFTY_WEEKLY_EXPIRY_WEEKDAY - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


def nifty_next_weekly_expiry(now: datetime | None = None) -> str:
    """Weekly expiry one cycle after the session's primary series."""
    now = now or ist_now()
    primary = parse_compact_expiry(nifty_weekly_expiry_for_session(now))
    if primary is None:
        return compact_expiry(nifty_weekly_expiry_on_or_after(now.date() + timedelta(days=7)))
    return compact_expiry(primary + timedelta(days=7))


def is_past_0dte_entry_cutoff(expiry: str | None, now: datetime | None = None) -> bool:
    """True when 0 DTE and current IST is after configured entry cutoff."""
    from config import ENTRY_CUTOFF_HOUR, ENTRY_CUTOFF_MINUTE

    now = now or ist_now()
    if not is_expiry_day(expiry, now):
        return False
    cutoff = now.replace(hour=ENTRY_CUTOFF_HOUR, minute=ENTRY_CUTOFF_MINUTE, second=0, microsecond=0)
    return now >= cutoff


def nifty_weekly_expiry_for_session(now: datetime | None = None) -> str:
    """
    Expiry series to load for the current session.
    On Tuesday before 15:30 IST → today's weekly; otherwise next Tuesday.
    """
    now = now or ist_now()
    today = now.date()
    close = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if today.weekday() == NIFTY_WEEKLY_EXPIRY_WEEKDAY and now < close:
        return compact_expiry(today)

    if today.weekday() == NIFTY_WEEKLY_EXPIRY_WEEKDAY and now >= close:
        return compact_expiry(today + timedelta(days=7))

    return compact_expiry(nifty_weekly_expiry_on_or_after(today))


def resolve_chain_expiry(service) -> str:
    """Pick chain expiry: today's Tue weekly when applicable, else broker nearest."""
    preferred = nifty_weekly_expiry_for_session()
    try:
        chain = service.get_options_chain(expiry=preferred)
        if chain is not None and not getattr(chain, "empty", True):
            return getattr(chain, "attrs", {}).get("expiry") or preferred
    except Exception:
        pass

    nearest = service._next_available_expiry()
    if nearest:
        return nearest
    return preferred


def expiry_context_block(expiry: str | None, now: datetime | None = None) -> str:
    """Factual expiry lines for LLM prompts."""
    now = now or ist_now()
    dte = days_to_expiry(expiry, now)
    exp_date = parse_compact_expiry(expiry)
    exp_label = exp_date.isoformat() if exp_date else "unknown"
    on_day = is_expiry_day(expiry, now)

    lines = [
        f"- Option expiry (chain): {expiry or 'unknown'} ({exp_label})",
        f"- Days to expiry (DTE): {dte if dte is not None else 'unknown'}",
        f"- Is expiry day (0 DTE): {'YES' if on_day else 'NO'}",
    ]
    if on_day:
        lines.append(
            "- EXPIRY DAY RULES: Elevated gamma/pin risk; prefer wider iron condor (different short strikes) "
            "over tight iron fly (same short strike). Cap confidence at MEDIUM for new short premium on 0 DTE "
            "unless range is very clear. Mention pin/max-pain into close."
        )
    return "\n".join(lines)


def session_phase_with_expiry(expiry: str | None, now: datetime | None = None) -> tuple[str, str]:
    """IST label and phase; uses EXPIRY_DAY when DTE=0 during market hours."""
    from services.analysis_digest import session_phase_label

    label, phase = session_phase_label(now)
    if is_expiry_day(expiry, now) and phase in ("REGULAR_SESSION", "OPENING_HOUR", "CLOSING_HOUR"):
        phase = "EXPIRY_DAY"
    return label, phase
