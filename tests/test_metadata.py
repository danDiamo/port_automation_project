"""
This file holds unit tests for metadata.py.
"""

# TODO: Inspect/review

from __future__ import annotations
from pathlib import Path

import pandas as pd
import pytest

from metadata_schema import METADATA_FIELDS
from metadata import CollectionMetadata, ScoreMetadata


# =============================================================================
# UNIT TESTS
# =============================================================================


def test_create_score_metadata_row_update_happy_path():
    """
    Happy path: ScoreMetadata builds a row-update dict keyed by unique itma_id.
    """
    smd = ScoreMetadata(itma_id="alpha")
    smd.set("soundslice_iframe", "https://www.soundslice.com/slices/x/embed/")
    smd.update({"time_signature": "4/4"})

    row_update = smd.update_row()

    assert isinstance(row_update, dict)
    assert list(row_update.keys()) == ["alpha"]
    assert row_update["alpha"]["soundslice_iframe"].startswith("https://www.soundslice.com")
    assert row_update["alpha"]["time_signature"] == "4/4"


def test_load_collection_metadata_allows_missing_schema_fields(tmp_path: Path):
    """
    Input CSV may be missing schema fields.
    Test that they are successfully added as empty columns on load.
    """
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text(
        "slug,title\n"
        "alpha,Alpha Title\n"
        "beta,Beta Title\n",
        encoding="utf-8",
    )

    md = CollectionMetadata(str(csv_path))
    df = md.load_collection_metadata()

    assert isinstance(df, pd.DataFrame)
    assert "slug" in df.columns
    assert "title" in df.columns

    # Missing schema fields should be present after load (as empty columns)
    for col in METADATA_FIELDS:
        assert col in df.columns

    row = md.get_score_metadata("alpha")
    assert isinstance(row, dict)
    assert row["slug"] == "alpha"
    assert row["title"] == "Alpha Title"


def test_upsert_row_updates_creates_missing_row_and_populates_fields(tmp_path: Path):
    """
    Upsert should allow adding a new row for a score that is not already
    present in the input CSV.
    It should
    (a) create the new row
    (b) apply updates
    (c) populate constants
    """
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text(
        "slug,title\n"
        "alpha,Alpha Title\n",
        encoding="utf-8",
    )

    md = CollectionMetadata(str(csv_path))
    md.load_collection_metadata()

    new_slug = "beta"
    url = "https://www.soundslice.com/slices/x/embed/"
    md.upsert_row_updates({new_slug: {"soundslice_iframe": url}})

    assert md.metadata is not None
    assert new_slug in md.metadata.index
    assert md.metadata.loc[new_slug, "slug"] == new_slug
    assert md.metadata.loc[new_slug, "soundslice_iframe"] == url

    # Constants should be populated for newly created rows too
    assert md.metadata.loc[new_slug, "image_alt_text"] == "Musical Notation"
    assert md.metadata.loc[new_slug, "explore_tag"] == "Port"
    assert md.metadata.loc[new_slug, "score_track_rights"] == "In Copyright"
    assert md.metadata.loc[new_slug, "score_track2_rights"] == "In Copyright"


def test_apply_row_updates_rejects_missing_slug(tmp_path: Path):
    """
    Strict apply_row_updates should fail if the update includes an IRMA id
    value that is not already present in the metadata table.
    """
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text(
        "slug,title\n"
        "alpha,Alpha Title\n",
        encoding="utf-8",
    )

    md = CollectionMetadata(str(csv_path))
    md.load_collection_metadata()

    with pytest.raises(ValueError) as e:
        md.apply_row_updates({"beta": {"soundslice_iframe": "https://www.soundslice.com/slices/x/embed/"}})

    assert "not present in metadata" in str(e.value)


def test_apply_row_updates_and_enforce_constants_happy_path(tmp_path: Path):
    """
    Happy path: apply_row_updates updates overwriteable fields and populates
    constants.
    """
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text(
        "slug,title\n"
        "alpha,Alpha Title\n",
        encoding="utf-8",
    )

    md = CollectionMetadata(str(csv_path))
    md.load_collection_metadata()

    url = "https://www.soundslice.com/slices/x/embed/"
    md.apply_row_updates({"alpha": {"soundslice_iframe": url}})

    assert md.metadata is not None
    assert md.metadata.loc["alpha", "soundslice_iframe"] == url

    # Constants should be populated
    assert md.metadata.loc["alpha", "image_alt_text"] == "Musical Notation"
    assert md.metadata.loc["alpha", "explore_tag"] == "Port"
    assert md.metadata.loc["alpha", "score_track_rights"] == "In Copyright"
    assert md.metadata.loc["alpha", "score_track2_rights"] == "In Copyright"


def test_collection_metadata_save_outputs_full_schema(tmp_path: Path):
    """
    Save should always write a CSV that matches the schema
    (all METADATA_FIELDS present).
    """
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text(
        "slug,title\n"
        "alpha,Alpha Title\n",
        encoding="utf-8",
    )

    md = CollectionMetadata(str(csv_path))
    md.load_collection_metadata()

    url = "https://www.soundslice.com/slices/x/embed/"
    md.apply_row_updates({"alpha": {"soundslice_iframe": url}})

    out_path = md.save()
    out_file = Path(out_path)
    assert out_file.exists()
    assert out_file.stat().st_size > 0

    saved_df = pd.read_csv(out_file, encoding="utf-8-sig")
    assert set(saved_df.columns) == set(METADATA_FIELDS)
    assert "slug" in saved_df.columns
    assert "soundslice_iframe" in saved_df.columns

    row = saved_df.loc[saved_df["slug"] == "alpha"].iloc[0]
    assert row["soundslice_iframe"] == url

# TODO: Expand this test suite if required as integration and
#  real-world testing proceeds
