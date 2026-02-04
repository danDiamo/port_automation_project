"""
This file holds unit and integration tests for collection.py.

Style notes:
  - Keep tests simple and happy-path focused.
  - Use tmp_path fixtures to build minimal, real filesystem structures.
  - Soundslice integration tests use the real API and must clean up.
"""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any

import pytest

from collection import Collection
from metadata import CollectionMetadata
from score import Score
import score as score_module


def _happy_score_processor(
    score: Score,
    slug: str,
    soundslice_folder_id: int | None,
) -> dict[str, dict[str, Any]]:
    """
    Minimal, picklable score processor for Collection happy-path tests.

    This avoids external services and expensive parsing; we only validate
    Collection orchestration and saving behavior here.
    """
    return {slug: {"processed": True, "folder_id": soundslice_folder_id}}


def test_run_saves_output_happy_path(tmp_path: Path):
    """
    Happy path: Collection.run(save=True) writes a processed CSV and returns
    the output path.

    We keep this integration-lite (filesystem + real metadata load/save) and
    explicitly disable Soundslice operations.
    """
    # Set up a minimal collection root with the expected XML directory.
    collection_root = tmp_path / "HappyCollection"
    collection_root.mkdir(parents=True)

    xml_dir = collection_root / f"{collection_root.name}_xml"
    xml_dir.mkdir(parents=True)

    # Create a tiny metadata CSV with the required Slug column.
    metadata_csv = tmp_path / "metadata.csv"
    metadata_csv.write_text("Slug\nalpha\nbeta\n", encoding="utf-8")

    # Create dummy XML files corresponding to the slugs.
    (xml_dir / "alpha.xml").write_text("<score-partwise/>", encoding="utf-8")
    (xml_dir / "beta.xml").write_text("<score-partwise/>", encoding="utf-8")

    # Load real CollectionMetadata (this test is allowed to exercise load/save).
    metadata = CollectionMetadata(str(metadata_csv))
    metadata.load_collection_metadata()

    # Build the Collection and run the pipeline.
    collection = Collection(
        metadata=metadata,
        collection_root=collection_root,
        score_processor=_happy_score_processor,
    )

    out_path = collection.run(
        parallel=False,
        save=True,
        soundslice=False,
    )

    # Assert we got a path back and it exists on disk.
    assert out_path is not None
    out_file = Path(out_path)
    assert out_file.exists()
    assert out_file.stat().st_size > 0


@pytest.mark.integration
def test_collection_soundslice_integration_run_happy_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Integration test using real Soundslice API:
      - creates a unique Soundslice folder
      - creates a slice via Score.create_soundslice_slice_and_get_embed_url
      - applies patch to CollectionMetadata, filling 'Soundslice_iframe' field
      - saves metadata and verifies Soundslice_iframe was written
      - cleans up the slice and folder

    To run this test, set the following environment variables in .env:
      RUN_SOUNDSLICE_INTEGRATION_TESTING=y
      APPLICATION_ID, PASSWORD
    """

    if os.getenv("RUN_SOUNDSLICE_INTEGRATION_TESTING") != "y":
        pytest.skip(
            "Set RUN_SOUNDSLICE_INTEGRATION_TESTING=y "
            "to run Soundslice integration tests."
        )

    if not os.getenv("APPLICATION_ID") or not os.getenv("PASSWORD"):
        pytest.skip(
            "Missing Soundslice credentials. "
            "Set APPLICATION_ID and PASSWORD in .env."
        )

    # Import our MusicXML test file and copy it into our temp
    # collection using the slug as the filename stem.
    from tests.test_score import happy_testfile

    slug = "integration-slug"
    folder_name = f"PYTEST_COLLECTION_{secrets.token_hex(8)}"

    # Collection expects: <collection_root>/<collection_root.name>_xml/
    collection_root = tmp_path / folder_name
    collection_root.mkdir(parents=True)

    xml_dir = collection_root / f"{collection_root.name}_xml"
    xml_dir.mkdir(parents=True)

    score_path = xml_dir / f"{slug}.xml"
    shutil.copy(happy_testfile, score_path)

    # Minimal metadata: Slug + Title are sufficient for the Soundslice method.
    # Metadata tests in test_metadata.py
    metadata_csv = tmp_path / "metadata.csv"
    metadata_csv.write_text(
        "Slug,Title\n"
        f"{slug},Pytest Slice {slug}\n",
        encoding="utf-8",
    )

    metadata = CollectionMetadata(str(metadata_csv))
    metadata.load_collection_metadata()

    # Capture Soundslice object ids for cleanup (folder_id + scorehash).
    created: dict[str, Any] = {"folder_id": None, "scorehash": None}

    # Wrap the real Soundslice client so we can capture create_slice details.
    RealClient = score_module.Client

    # copied from test_score (consider refactoring?)
    class CapturingClient:
        """
        Captures created folder_id and scorehash while delegating all real
        operations to the real Soundslice client.
        """

        def __init__(self, application_id: str, password: str):
            self._real = RealClient(application_id, password)

        def list_folders(self):
            return self._real.list_folders()

        def create_folder(self, name: str):
            return self._real.create_folder(name=name)

        def create_slice(self, **kwargs):
            created["folder_id"] = kwargs.get("folder_id")
            resp = self._real.create_slice(**kwargs)
            created["scorehash"] = resp.get("scorehash")
            return resp

        def upload_slice_notation(self, *, scorehash: str, fp):
            return self._real.upload_slice_notation(scorehash=scorehash, fp=fp)

        def delete_slice(self, scorehash: str):
            return self._real.delete_slice(scorehash)

        def delete_folder(self, *, folder_id: int):
            return self._real.delete_folder(folder_id=folder_id)

    monkeypatch.setattr(score_module, "Client", CapturingClient)

    def soundslice_score_processor(
        score: Score,
        slug_in: str,
        soundslice_folder_id: int | None,
    ) -> dict[str, dict[str, Any]]:
        """
        Test score processor to test our Soundslice API integration.
        """
        soundslice_url = score.create_soundslice_slice_and_get_embed_url(
            collection_metadata=metadata,
            slug=slug_in,
            _folder_id=soundslice_folder_id,
        )
        return {slug_in: {"Soundslice_iframe": soundslice_url}}

    collection = Collection(
        metadata=metadata,
        collection_root=collection_root,
        score_processor=soundslice_score_processor,
    )

    # Run with Soundslice enabled so Collection creates/gets the folder once
    # and passes the folder_id to the score processor.
    try:
        out_path = collection.run(
            parallel=False,
            max_workers=None,
            save=True,
            soundslice=True,
        )

        assert isinstance(out_path, str)
        out_file = Path(out_path)
        assert out_file.exists()
        assert out_file.stat().st_size > 0

        # Verify we created a slice and captured IDs for cleanup.
        assert created.get("scorehash"), (
            "Did not capture scorehash from Soundslice create_slice response."
        )
        assert created.get("folder_id"), (
            "Did not capture folder_id from Soundslice create_slice call."
        )

        # Verify the Soundslice_iframe field was populated in the output CSV.
        saved_text = out_file.read_text(encoding="utf-8")
        assert "Soundslice_iframe" in saved_text

        # The embed URL is expected to include the Soundslice domain.
        assert "https://www.soundslice.com" in saved_text

    finally:
        # Cleanup: delete slice first, then folder.
        try:
            from soundsliceapi import Client as CleanupClient

            app_id, pwd = score_module.get_soundslice_credentials_from_env()
            cleanup_client = CleanupClient(app_id, pwd)

            if created.get("scorehash"):
                cleanup_client.delete_slice(str(created["scorehash"]))

            if created.get("folder_id"):
                cleanup_client.delete_folder(folder_id=int(created["folder_id"]))

        except Exception as cleanup_err:
            print(f"Soundslice cleanup warning (non-fatal): {cleanup_err}")

