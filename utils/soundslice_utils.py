"""
soundslice_utils.py holds helper functions for Soundslice integration.
Note: This module is not currently unit tested but is used by tests in
test_score.py.
"""

from __future__ import annotations

import os

from soundsliceapi import Client as SoundsliceClient


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


def check_soundslice_folder_exists(folder_name: str) -> int:
    """
    Check that a Soundslice folder exists and return its id.

    Fail-fast:
      - Raises error on API/credential/permission errors.
      - Tolerates parallel processing race case where another
        process created the folder first, then re-lists to obtain folder id.
    """
    folder_name = str(folder_name).strip()
    if not folder_name:
        raise ValueError("folder_name must be a non-empty string.")

    application_id, password = get_soundslice_credentials_from_env()
    client = SoundsliceClient(application_id, password)

    def _find_folder_id() -> int | None:
        for f in client.list_folders():
            if f.get("name") == folder_name:
                fid = f.get("id")
                return int(fid) if fid is not None else None
        return None

    folder_id = _find_folder_id()
    if folder_id is not None:
        return folder_id

    try:
        client.create_folder(name=folder_name)
    except Exception as e:
        msg = str(e).lower()
        race_ok = any(
            needle in msg
            for needle in (
                "already exists",
                "already have",
                "duplicate",
                "conflict",
                "409",
            )
        )
        if not race_ok:
            raise RuntimeError(
                f"Failed to create Soundslice folder '{folder_name}': {e}"
            ) from e

    folder_id = _find_folder_id()
    if folder_id is None:
        raise RuntimeError(
            f"Failed to resolve Soundslice folder id for '{folder_name}'."
        )

    return folder_id