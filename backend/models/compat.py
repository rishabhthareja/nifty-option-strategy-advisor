"""Typing compatibility for Python < 3.8 (Literal, etc.)."""

import sys

if sys.version_info >= (3, 8):
    from typing import Literal
else:
    from typing_extensions import Literal

__all__ = ["Literal"]
