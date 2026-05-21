"""
soundslice_utils.py holds helper functions for Soundslice integration.
Note: This module is not currently unit tested but is used by tests in
test_score.py.
"""

from __future__ import annotations

import os
import warnings

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


def create_soundslice_list(collection_name: str) -> str | None:
    """
    Create a Soundslice list for a collection.
    
    Args:
        collection_name: Name for the list (typically collection_root.name)
    
    Returns:
        list_id as string, or None if creation fails
    """
    collection_name = str(collection_name).strip()
    if not collection_name:
        warnings.warn(
            "Cannot create Soundslice list: collection name is blank/empty.",
            UserWarning,
        )
        return None
    
    try:
        application_id, password = get_soundslice_credentials_from_env()
        client = SoundsliceClient(application_id, password)
        
        response = client.create_list(name=collection_name)
        list_id = response.get("id")
        
        if not list_id:
            warnings.warn(
                f"Soundslice API did not return list_id for '{collection_name}'.",
                UserWarning,
            )
            return None
        
        return str(list_id)
    
    except Exception as e:
        warnings.warn(
            f"Failed to create Soundslice list '{collection_name}': {e}",
            UserWarning,
        )
        return None


def add_slices_to_soundslice_list(
    list_id: str, 
    scorehashes: list[str]
) -> bool:
    """
    Add multiple slices to a Soundslice list in one batch operation.
    
    Args:
        list_id: The Soundslice list ID
        scorehashes: List of scorehash strings to add
    
    Returns:
        True if successful, False otherwise
    """
    if not list_id or not isinstance(list_id, str):
        warnings.warn(
            "Cannot add slices to list: list_id is invalid.",
            UserWarning,
        )
        return False
    
    if not scorehashes:
        # Nothing to add - this is not an error
        return True
    
    try:
        application_id, password = get_soundslice_credentials_from_env()
        client = SoundsliceClient(application_id, password)
        
        client.add_slices_to_list(
            list_id=list_id,
            scorehashes=scorehashes,
        )
        
        return True
    
    except Exception as e:
        warnings.warn(
            f"Failed to add {len(scorehashes)} slice(s) to Soundslice list: {e}",
            UserWarning,
        )
        return False
