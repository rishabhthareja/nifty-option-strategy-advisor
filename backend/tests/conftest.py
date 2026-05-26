"""Pytest bootstrap — enforce supported Python version."""

import sys

import pytest

MIN_PYTHON = (3, 10)


def pytest_configure(config):
    if sys.version_info < MIN_PYTHON:
        pytest.exit(
            f"This project requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+. "
            f"You are running {sys.version.split()[0]} ({sys.executable}).\n"
            "Install Python 3.10+ and run:\n"
            "  py -3.10 -m venv .venv\n"
            "  .venv\\Scripts\\activate\n"
            "  pip install -r requirements.txt\n"
            "  python -m pytest tests/ -q",
            returncode=1,
        )
