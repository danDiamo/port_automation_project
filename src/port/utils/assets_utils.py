"""This file holds utility functions for mapping paths to package assets."""

from __future__ import annotations

from pathlib import Path


def assets_dir() -> Path:
    """Return the absolute path to the package-owned assets directory."""
    return Path(__file__).resolve().parents[1] / "assets"


def asset_path(name: str) -> Path:
    """Return the absolute path to a package asset file."""
    return assets_dir() / name