# tests/conftest.py
from __future__ import annotations

from dotenv import load_dotenv

def pytest_configure(config):
    # Load repo .env for tests (does not override already-set env vars)
    load_dotenv(override=False)