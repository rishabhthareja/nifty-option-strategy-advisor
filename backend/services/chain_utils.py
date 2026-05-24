"""Option chain helpers (lot size) without LangChain dependencies."""

from __future__ import annotations


def lot_size_from_chain(chain) -> int:
    from config import LOT_SIZE

    if chain is not None and "lot_size" in chain.columns and not chain["lot_size"].empty:
        try:
            raw = int(chain["lot_size"].mode().iloc[0])
            if raw > 0:
                return raw
        except (IndexError, TypeError, ValueError):
            pass

    return LOT_SIZE


def order_quantity(chain) -> int:
    from config import NUM_LOTS

    return lot_size_from_chain(chain) * NUM_LOTS
