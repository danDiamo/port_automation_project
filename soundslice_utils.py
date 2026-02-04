"""soundslice_utils.py holds helper functions for Soundslice integration."""

from __future__ import annotations

import os


def get_soundslice_credentials_from_env() -> tuple[str, str]:
    """
    Get Soundslice credentials from environment variables.

    Required keys (internal naming convention -- please do not break):
      - APPLICATION_ID
      - PASSWORD
    """
    app_id = os.getenv("APPLICATION_ID")
    pwd = os.getenv("PASSWORD")

    if not app_id or not pwd:
        raise RuntimeError(
            "Soundslice credentials not found. Ensure the environment contains "
            "APPLICATION_ID and PASSWORD fields for Soundslice."
        )

    return app_id, pwd