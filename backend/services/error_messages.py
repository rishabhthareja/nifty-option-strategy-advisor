"""Map agent exceptions to user-facing SSE error payloads."""
import re


def format_sse_error(exc: Exception) -> dict:
    raw = str(exc).strip()
    lower = raw.lower()

    if _is_gemini_rate_limit(lower):
        retry_seconds = _extract_retry_seconds(lower)
        return {
            "agent": "error",
            "code": "gemini_rate_limit",
            "message": (
                f"Gemini API rate limit reached. Wait about {retry_seconds} seconds, "
                "then click Run Analysis again."
            ),
            "hint": (
                "Free tier allows very few requests per minute. Enable billing in Google AI Studio, "
                "or set GEMINI_MODEL in backend/.env to a model with spare quota (e.g. gemini-3-flash-preview)."
            ),
            "retry_seconds": retry_seconds,
            "links": {
                "rate_limits": "https://ai.dev/rate-limit",
                "billing": "https://aistudio.google.com/",
            },
            "detail": _truncate(raw, 1200) if len(raw) > 120 else None,
        }

    return {
        "agent": "error",
        "code": "analysis_failed",
        "message": _truncate(raw, 400),
        "hint": "Check the backend terminal for the full stack trace.",
    }


def _is_gemini_rate_limit(lower: str) -> bool:
    markers = (
        "resource_exhausted",
        "429",
        "quota",
        "rate limit",
        "rate_limit",
        "too many requests",
    )
    return any(m in lower for m in markers)


def _extract_retry_seconds(lower: str) -> int:
    match = re.search(r"retry in ([\d.]+)s", lower)
    if match:
        return max(int(float(match.group(1))), 1)
    return 60


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
