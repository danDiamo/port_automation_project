"""
This file holds unit tests for metadata.py.
"""

from __future__ import annotations
from pathlib import Path
import pandas as pd

from metadata import CollectionMetadata, ScoreMetadata


# =============================================================================
# UNIT TESTS (Happy Path)
# =============================================================================


def test_create_score_metadata_patch_happy_path():
    """
    Happy path: ScoreMetadata builds a metadata patch dict keyed by unique
    slug identifier.
    """

    # Create a mock ScoreMetadata obj and add fields.
    smd = ScoreMetadata(slug="alpha")
    smd.set("Soundslice_iframe", "https://www.soundslice.com/slices/x/embed/")
    smd.update({"time_signature": "4/4"})

    # Convert to patch format required by CollectionMetadata.apply_patches().
    patch = smd.as_patch()

    # Assert patch shape and content.
    assert isinstance(patch, dict)
    assert list(patch.keys()) == ["alpha"]
    assert patch["alpha"]["Soundslice_iframe"].startswith(
        "https://www.soundslice.com"
    )
    assert patch["alpha"]["time_signature"] == "4/4"


def test_load_collection_metadata_and_extract_score_metadata_happy_path(
    tmp_path: Path,
):
    """
    Happy path: CollectionMetadata loads a CSV and extracts a single row
    corresponding to one Score, formatted as a dict, with slug as unique
    identifier.
    """

    # Write a small CSV. Slug is the only hard requirement for schema validity.
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text(
        "Slug,Title\n"
        "alpha,Alpha Title\n"
        "beta,Beta Title\n",
        encoding="utf-8",
    )

    # create CollectionMetadata obj
    md = CollectionMetadata(str(csv_path))

    # Load the CSV into CollectionMetadata & check type.
    df = md.load_collection_metadata()
    assert isinstance(df, pd.DataFrame)

    # Look up one row by slug as a dict.
    row = md.get_score_metadata("alpha")
    assert isinstance(row, dict)
    assert row["Slug"] == "alpha"
    assert row["Title"] == "Alpha Title"


def test_apply_patches_to_collection_metadata_happy_path(
    tmp_path: Path,
):
    """
    Happy path: apply_patches updates CollectionMetadata to
    hold a new Score's Soundslice_iframe and enforce
    constants.
    """
    # Start with a minimal metadata CSV.
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text(
        "Slug,Title\n"
        "alpha,Alpha Title\n",
        encoding="utf-8",
    )

    md = CollectionMetadata(str(csv_path))
    md.load_collection_metadata()

    # update an overwriteable field using CollectionMetadata.apply_patches()
    url = "https://www.soundslice.com/slices/x/embed/"
    md.apply_patches({"alpha": {"Soundslice_iframe": url}})

    # Verify CollectionMetadata DataFrame content reflects the patch.
    assert md.metadata is not None
    assert md.metadata.loc["alpha", "Soundslice_iframe"] == url

    # Verify constants are enforced for this patch.
    assert md.metadata.loc["alpha", "Image_alt_text"] == "Musical Notation"
    assert md.metadata.loc["alpha", "collection_tag"] == "Port"
    assert md.metadata.loc["alpha", "score_track_rights"] == "In Copyright"
    assert md.metadata.loc["alpha", "score_track2_rights"] == "In Copyright"


def test_collection_metadata_save_round_trip_happy_path(tmp_path: Path):
    """
    Happy path: CollectionMetadata.save() writes a processed CSV,
    which contains our updated Soundslice_iframe value.
    """

    # Create a minimal CSV.
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text(
        "Slug,Title\n"
        "alpha,Alpha Title\n",
        encoding="utf-8",
    )

    md = CollectionMetadata(str(csv_path))
    md.load_collection_metadata()

    # Apply a patch to change Soundslice url.
    url = "https://www.soundslice.com/slices/x/embed/"
    md.apply_patches({"alpha": {"Soundslice_iframe": url}})

    # Save and verify the file exists.
    out_path = md.save()
    out_file = Path(out_path)
    assert out_file.exists()
    assert out_file.stat().st_size > 0

    # Read the saved CSV and assert the exact value was written.
    saved_df = pd.read_csv(out_file)

    # Indexing details can vary (save uses index=False by default), so we
    # locate the row by Slug column.
    row = saved_df.loc[saved_df["Slug"] == "alpha"].iloc[0]
    assert row["Soundslice_iframe"] == url

# TODO: Expand this test suite if required as integration and
#  real-world testing proceeds
