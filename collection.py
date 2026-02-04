"""This file holds a 'Collection' Python class (and helper functions),
modeling a collection of digital music scores"""

# built-in imports
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
# external imports
from soundsliceapi import Client as SoundsliceClient
# local imports
from metadata import CollectionMetadata
from score import Score
from soundslice_utils import get_soundslice_credentials_from_env

# type hint
ScoreProcessor = Callable[[Score, str, int | None], dict[str, dict[str, Any]]]


def _process_single_score_score_worker(
    score_path_str: str,
    collection_root_str: str | None,
    score_processor: ScoreProcessor,
    soundslice_folder_id: int | None,
) -> dict[str, dict[str, Any]]:

    """
    Worker entrypoint for parallel processing a single score.

    Design:
        This function is top-level (module scope) so it is picklable on both
        macOS and Windows.

    Behavior:
        - Reconstructs Paths from strings passed across process boundaries.
        - Derives score Slug from filepath.
        - Instantiates a Score object and calls the score_processor.
        - Validates that score_processor returns a dict containing slug key.

    Args:
        score_path_str: Filepath to a single MusicXML score (as str).
        collection_root_str: Collection root directory (as str) or None.
        score_processor: Callable implementing score processing logic.
        soundslice_folder_id: Optional; if provided, can be passed
        into score processing to avoid repeated Soundslice folder lookups.

    Returns:
        A patch dict formatted per::
            {Slug: {field: value}}
    """

    # set up in paths
    score_path = Path(score_path_str)
    collection_root = Path(collection_root_str) if collection_root_str else None
    # derive slug from score_path
    slug = score_path.stem.strip()
    if not slug:
        raise ValueError(f"Slug cannot be derived from score_path for"
                         f" {score_path}")

    # setup Score object and call score_processor on it
    score = Score(score_path, collection_root=collection_root)
    patch = score_processor(score, slug, soundslice_folder_id)

    if not isinstance(patch, dict) or slug not in patch:
        raise ValueError(
            "score_processor must return a patch dict shaped like "
            "{slug: {field: value}} and must include the slug as unique id. "
            f"Instead, we got {list(patch.keys())!r}"
        )

    return patch


@dataclass
class Collection:
    """
    Orchestrates collection-level processing:
      - reads id slugs from a metadata CSV
      - resolves score paths from <collection_root>/<collection_root.name>_xml/
      - runs processing on each Score to produce metadata patches
      - applies patches to CollectionMetadata
    """

    # class-level type annotations
    metadata: CollectionMetadata
    collection_root: Path
    score_processor: ScoreProcessor

    # class paths derived during init from metadata & filesystem
    score_paths: list[Path] | None = None
    # dataclass-specific post-constructor method
    def __post_init__(self) -> None:
        """
        Post-init hook for dataclass construction.

        Normalizes collection_root str to a Path and resolves score_paths
        from the metadata CSV (fail-fast if any expected score file is
        missing).
        """
        # Normalize paths
        self.collection_root = Path(self.collection_root)
        # Resolve score_paths once during init (fail-fast)
        self.score_paths = self._resolve_score_paths_from_metadata()

    def _xml_dir(self) -> Path:
        """
        Return the expected XML folder for this collection.
        Expected:
            <collection_root>/<collection_root.name>_xml/
        """
        return self.collection_root / f"{self.collection_root.name}_xml"

    def _extract_slugs_from_metadata(self) -> list[str]:
        """
        Read slugs from metadata CSV.

        Requirement:
            Metadata must have a 'Slug' column (upper-case required).
        """
        # TODO: discuss slug column case sensitivity with ITMA

        if self.metadata.metadata is None:
            self.metadata.load_collection_metadata()

        md = self.metadata.metadata
        assert md is not None

        if "Slug" not in md.columns:
            raise ValueError("Metadata CSV is missing required 'Slug' column.")
        # extract slugs
        slugs = md["Slug"].astype(str).str.strip().tolist()

        # Drop blanks / NaN strings
        slugs = [s for s in slugs if s and s.strip().lower() != "nan"]

        if not slugs:
            raise ValueError("Metadata CSV contains no usable Slug values.")

        return slugs

    def _resolve_score_paths_from_metadata(self) -> list[Path]:
        """
        Resolve a list of score paths from metadata slugs.

        Fail-fast:
          - Raises FileNotFoundError if any slug has no matching file.
        """
        xml_dir = self._xml_dir()
        if not xml_dir.exists():
            raise FileNotFoundError(
                f"Expected XML directory does not exist: {xml_dir}")

        score_paths: list[Path] = []
        missing: list[str] = []

        for slug in self._extract_slugs_from_metadata():
            # Each slug should correspond to exactly one local file.
            score_path = xml_dir / f"{slug}.xml"
            if not score_path.exists():
                missing.append(slug)
                continue

            score_paths.append(score_path)

        if missing:
            preview = ", ".join(missing[:10])
            raise FileNotFoundError(
                "Metadata contains slug(s) with no matching score file in "
                f"{xml_dir}: {preview}" + (" ..." if len(missing) > 10 else "")
            )

        return score_paths

    def ensure_soundslice_folder_exists(self) -> int:
        """
        Ensure the Soundslice folder for this collection exists and return its
         id.

        Folder naming policy:
            Soundslice folder name mirrors local collection_root.name

        Fail-fast:
            - Raises immediately on real API/credential/permission errors.
            - Tolerates the expected parallel processing race case where
            another process created the folder first
            (then re-lists to obtain id).
        """
        folder_name = self.collection_root.name

        application_id, password = get_soundslice_credentials_from_env()
        client = SoundsliceClient(application_id, password)

        def _find_folder_id() -> int | None:
            # hidden helper function to get Soundslice folder id number
            for f in client.list_folders():
                if f.get("name") == folder_name:
                    fid = f.get("id")
                    return int(fid) if fid is not None else None
            return None

        # run helper 
        folder_id = _find_folder_id()
        if folder_id is not None:
            return folder_id

        # create Soundslice folder if needed (parallel-safe)
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

        # last effort to get folder id after trying to find it 
        # and trying to create it
        folder_id = _find_folder_id()
        if folder_id is None:
            raise RuntimeError(
                f"Failed to resolve Soundslice folder id for '{folder_name}'."
            )

        return folder_id

    def process_single_score(
        self,
        score_path: Path,
        *,
        soundslice_folder_id: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Process a single score and return its patch.
        Does not update metadata automatically but stores the metadata patch.
        (Call apply_patches to update).
        """
        # setup paths
        score_path = Path(score_path)
        slug = score_path.stem.strip()
        if not slug:
            raise ValueError(f"Cannot derive slug for score {score_path}")
        # setup Score object
        score = Score(score_path, collection_root=self.collection_root)
        # process it using score_processor
        patch = self.score_processor(score, slug, soundslice_folder_id)

        if not isinstance(patch, dict) or slug not in patch:
            raise ValueError(
                "score_processor must return a patch dict shaped like "
                "{slug: {field: value}} and must include the slug as unique "
                f"id. Instead, we got {list(patch.keys())!r}"
            )

        return patch

    def process_all(
        self,
        *,
        parallel: bool = False,
        max_workers: int | None = None,
        soundslice_folder_id: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Process all scores, returning an aggregated patch map:
            {slug: {field: value}}

        Args:
            parallel: If True, uses process-based parallelism.
            max_workers: Passed through to ProcessPoolExecutor.
            soundslice_folder_id: Optional. If provided,
                it will be passed to score_processor and can be forwarded into
                Score methods to minimize Soundslice folder lookups.
        """
        assert self.score_paths is not None

        # type hinting
        patches: dict[str, dict[str, Any]] = {}

        # serial processing call
        if not parallel:
            for p in self.score_paths:
                patch = self.process_single_score(
                    p, soundslice_folder_id=soundslice_folder_id
                )
                for slug, values in patch.items():
                    patches.setdefault(slug, {}).update(values)
            return patches
        # parallel processing call
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = [
                ex.submit(
                    _process_single_score_score_worker,
                    str(p),
                    str(self.collection_root),
                    self.score_processor,
                    soundslice_folder_id,
                )
                for p in self.score_paths
            ]

            # merge outputs from parallel processing
            for fut in as_completed(futures):
                patch = fut.result()
                for slug, values in patch.items():
                    patches.setdefault(slug, {}).update(values)

        return patches

    def apply_patches(self, patches: dict[str, dict[str, Any]]) -> None:
        """Apply a patch map (update collection-level metadata)"""
        self.metadata.apply_patches(patches)

    def run(
            self,
            *,
            parallel: bool = False,
            max_workers: int | None = None,
            save: bool = True,
            soundslice: bool = False,
    ) -> str | None:
        """
        Flow control method to run the full collection pipeline.

        Flow:
          - Load the metadata CSV (if needed).
          - Process each score file (serially or in parallel)
          to build a patch map.
          - Apply patches to the in-memory metadata table.
          - Optionally save the updated table to disk.

        Notes:
            Score selection is via the metadata CSV "Slug" column.
            For each Slug, we expect a matching score file in:
                <collection_root>/<collection_root.name>_xml/
            Missing files cause a fail-fast error during Collection init.

        Args:
            parallel: If True, use process-based parallelism.
            max_workers: Passed through to ProcessPoolExecutor.
            NOTE: Be conservative with max_workers to avoid overloading the
            Soundslice API host. Try 4.
            save: If True, write the processed CSV to "<input>_processed.csv".
            soundslice: If True, connect to Soundslice API and run all
            Soundslice Score operations. If False, skip all Soundslice
            operations.

        Returns:
            Output path (as str) if save=True, otherwise None.
        """
        if self.metadata.metadata is None:
            self.metadata.load_collection_metadata()

        soundslice_folder_id: int | None = None
        if soundslice:
            soundslice_folder_id = self.ensure_soundslice_folder_exists()

        patches = self.process_all(
            parallel=parallel,
            max_workers=max_workers,
            soundslice_folder_id=soundslice_folder_id,
        )
        self.apply_patches(patches)

        if save:
            return self.metadata.save()

        return None