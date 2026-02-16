# TODO: add comments / inspect new content

"""
processing.py holds flow-control tools for collection and score processing.

- Batch input file selection defaults to all MusicXML files in
  "<collection_root>_xml" folder under collection root dir.
- Metadata CSV is optional. Its expected location is not hardcoded, for now;
  user must point to it manually.
- Per-score output: {itma_id: {field: value}} metadata patch dict is created
and 'upserted' to pandas metadata Dataframe (update + insert database
operation), which can be saved to CSV file.
- Fail-fast on the first score error and record the score id. For now.
- Windows/macOS agnostic
- Scores can be processed in parallel but this functionality is switched off by
 default.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from aws_utils import copy_mp3_to_aws
from metadata import CollectionMetadata
from score import Score
from soundslice_utils import ensure_soundslice_folder_exists


def _get_itma_id_from_path(score_path: Path) -> str:
    """
    Derives the ITMA ID from a given MusicXML score path.
    If the filename stem is empty, an exception is raised.

    Parameters:
    score_path: Path
        Input MusicXML score file path.

    Returns:
    str
        The derived ITMA ID.

    Raises:
    ValueError
        If the ITMA ID cannot be derived from the provided path.
    """
    itma_id = (score_path.stem or "").strip()
    if not itma_id:
        raise ValueError(
            f"Cannot derive ITMA id from score path: {score_path}"
        )
    return itma_id


@dataclass(frozen=True)
class _TitleLookupMetadata:
    """
    Picklable metadata lookup via a {itma_id -> title} dict.

    Calls the `get_score_metadata(itma_id) API used by
    Score.get_title(). This lets us keep serial and parallel
    behavior consistent, while avoiding passing a pandas-based
    CollectionMetadata object to subprocesses.
    """
    title_lookup: dict[str, str]

    def get_score_metadata(self, itma_id: str) -> dict[str, Any]:
        slug = str(itma_id or "").strip()
        if not slug:
            raise ValueError("itma_id slug is blank/empty.")
        if slug not in self.title_lookup:
            raise KeyError(slug)
        return {"title": self.title_lookup.get(slug, "")}


def _single_score_score_worker(
    *,
    score_path_str: str,
    collection_root_str: str,
    mode: str,
    analysis_methods: list[str] | None,
    derivative_methods: list[str] | None,
    soundslice_folder_id: int | None,
    title_lookup: dict[str, str] | None,
    has_metadata: bool,
) -> dict[str, dict[str, Any]]:
    """
    Process a single musical score. Parallel-friendly.

    Intentionally calls ScoreProcessor.process_single_score() so serial and
    parallel behavior stay consistent
    (helpful for title/metadata edge cases).
    """
    score_path = Path(score_path_str)
    collection_root = Path(collection_root_str)

    itma_id = _get_itma_id_from_path(score_path)
    context = CollectionContext(collection_root=collection_root)

    processing_steps = ScoreProcessingOrchestrator(
        mode=ProcessingMode(mode),
        analysis_methods=analysis_methods,
        derivative_methods=derivative_methods,
        parallel=False,
        max_workers=None,
        allow_new_rows=True,
    )

    metadata_for_title = (
        _TitleLookupMetadata(title_lookup)
        if (has_metadata and title_lookup is not None)
        else None
    )

    processor = ScoreProcessor()
    return processor.process_single_score(
        score_path=score_path,
        itma_id=itma_id,
        context=context,
        processing_steps=processing_steps,
        collection_metadata=metadata_for_title,
        soundslice_folder_id=soundslice_folder_id,
        custom_title=None,
        has_metadata=has_metadata,
    )


@dataclass(frozen=True)
class CollectionContext:
    """
    Manage context info for a score collection, including paths to XML and
    MP3 directories.
    """
    collection_root: Path

    @property
    def xml_dir(self) -> Path:
        """MusicXML directory path"""
        return self.collection_root / f"{self.collection_root.name}_xml"

    @property
    def incipit_mp3_dir(self) -> Path:
        """Incipit mp3 directory path"""
        return (
                self.collection_root /
                f"{self.collection_root.name}_incipit_mp3"
        )

    @property
    def performance_mp3_dir(self) -> Path:
        """Performance mp3 directory path"""
        return (
                self.collection_root /
                f"{self.collection_root.name}_performance_mp3"
        )

    @property
    def slow_mp3_dir(self) -> Path:
        """Slow mp3 directory path"""
        return (
                self.collection_root /
                f"{self.collection_root.name}_slow_mp3"
        )


class ScoreSelectionMode(str, Enum):
    """Manage input score selection via three user-selectable modes."""
    ALL_FILES_IN_XML_DIR = "all_files_in_xml_dir"
    SINGLE_FILE_BY_PATH = "single_file_by_path"
    SINGLE_FILE_BY_ITMA_ID = "single_file_by_itma_id"


@dataclass(frozen=True)
class ScoreSelection:
    """Select input scores for processing using ScoreSelectionMode modes."""
    by: ScoreSelectionMode
    score_path: Path | None = None
    itma_id: str | None = None

    @staticmethod
    def select_all_files_in_xml_dir() -> "ScoreSelection":
        """Select all .xml and/or .musicxml files in XML subdirectory"""
        return ScoreSelection(by=ScoreSelectionMode.ALL_FILES_IN_XML_DIR)

    @staticmethod
    def select_single_file_by_path(score_path: str | Path) -> "ScoreSelection":
        """Select a single score file by path"""
        return ScoreSelection(
            by=ScoreSelectionMode.SINGLE_FILE_BY_PATH,
            score_path=Path(score_path),
        )

    @staticmethod
    def select_single_file_by_itma_id(itma_id: str) -> "ScoreSelection":
        """Select a single score file by ITMA id ('slug' field value)"""
        return ScoreSelection(
            by=ScoreSelectionMode.SINGLE_FILE_BY_ITMA_ID,
            itma_id=str(itma_id),
        )


class ProcessingMode(str, Enum):
    """Class holding our score processing modes."""
    ANALYSIS = "analysis"
    DERIVATIVES = "derivatives"
    SOUNDSLICE = "soundslice"
    PASSTHROUGH_AWS = "passthrough_aws"
    ALL = "all"


@dataclass(frozen=True)
class ScoreProcessingOrchestrator:
    """Sets up options for score/collection processing"""
    mode: ProcessingMode = ProcessingMode.ALL
    analysis_methods: list[str] | None = None
    derivative_methods: list[str] | None = None
    parallel: bool = False
    max_workers: int | None = None
    allow_new_rows: bool = True


class ScoreProcessor:
    """
    Run score processing and returns a metadata patch dict, formatted per:
        {itma_id: {field: value}}
    """

    def process_single_score(
        self,
        *,
        score_path: Path,
        itma_id: str,
        context: CollectionContext,
        processing_steps: ScoreProcessingOrchestrator,
        collection_metadata: CollectionMetadata | None = None,
        soundslice_folder_id: int | None = None,
        custom_title: str | None = None,
        has_metadata: bool | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Process score and return metadata patch."""
        score = Score(score_path, collection_root=context.collection_root)

        score.title = score.get_title(
            custom_title=custom_title,
            collection_metadata=collection_metadata,
            itma_id=itma_id,
            has_metadata=has_metadata,
        )

        score_metadata_patch: dict[str, Any] = {"title": score.title}
        score_metadata_patch.update(
            self._run_processing_steps(
                score=score,
                itma_id=itma_id,
                context=context,
                processing_steps=processing_steps,
                soundslice_folder_id=soundslice_folder_id,
            )
        )
        return {itma_id: score_metadata_patch}

    def _run_processing_steps(
        self,
        *,
        score: Score,
        itma_id: str,
        context: CollectionContext,
        processing_steps: ScoreProcessingOrchestrator,
        soundslice_folder_id: int | None,
    ) -> dict[str, Any]:
        """
        Run the selected processing steps for a score and return a metadata
        patch.
        """
        mode = processing_steps.mode

        run_analysis = mode in {
            ProcessingMode.ANALYSIS,
            ProcessingMode.ALL
        }
        run_derivatives = mode in {
            ProcessingMode.DERIVATIVES,
            ProcessingMode.ALL
        }
        run_soundslice = mode in {
            ProcessingMode.SOUNDSLICE,
            ProcessingMode.ALL
        }
        run_passthrough_aws = mode in {
            ProcessingMode.PASSTHROUGH_AWS,
            ProcessingMode.ALL,
        }

        metadata_patch: dict[str, Any] = {}

        if run_analysis:
            metadata_patch.update(
                self._run_analysis_steps(
                    score=score,
                    processing_steps=processing_steps,
                )
            )

        if run_derivatives:
            metadata_patch.update(
                self._run_derivatives_steps(
                    score=score,
                    processing_steps=processing_steps,
                )
            )

        if run_soundslice:
            metadata_patch.update(
                self._run_soundslice_step(
                    score=score,
                    itma_id=itma_id,
                    soundslice_folder_id=soundslice_folder_id,
                )
            )

        if run_passthrough_aws:
            metadata_patch.update(
                self._run_passthrough_aws_step(
                    score=score,
                    itma_id=itma_id,
                    context=context,
                )
            )

        return metadata_patch

    def _run_analysis_steps(
        self,
        *,
        score: Score,
        processing_steps: ScoreProcessingOrchestrator,
    ) -> dict[str, Any]:
        selected_steps = set(processing_steps.analysis_methods or [])

        def select(name: str) -> bool:
            return (not selected_steps) or (name in selected_steps)

        out: dict[str, Any] = {}

        if select("key_signature"):
            out["key_signature"] = score.detect_key()

        if select("mode"):
            out["mode"] = score.extract_mode_from_key_signature()

        if select("tonic"):
            out["tonic"] = score.extract_tonic_from_key_signature()

        if select("time_signature"):
            out["time_signature"] = score.extract_time_signature()

        if select("number_of_parts"):
            out["number_of_parts"] = score.count_number_of_parts()

        if select("bb_code"):
            out["bb_code"] = score.create_breathnach_codes()

        return out

    def _run_derivatives_steps(
        self,
        *,
        score: Score,
        processing_steps: ScoreProcessingOrchestrator,
    ) -> dict[str, Any]:
        selected = set(processing_steps.derivative_methods or [])

        def select(name: str) -> bool:
            return (not selected) or (name in selected)

        out: dict[str, Any] = {}

        if select("pdf_download"):
            out["pdf_download"] = score.convert_score_to_pdf()

        if select("featured_image"):
            out["featured_image"] = score.convert_incipit_to_svg()

        if select("midi_audio_full"):
            out["midi_audio_full"] = score.convert_score_to_midi()

        if select("incipit_audio"):
            out["incipit_audio"] = score.convert_incipit_to_mp3()

        if select("abc_notation"):
            out["abc_notation"] = score.convert_score_to_abc()

        return out

    def _run_soundslice_step(
        self,
        *,
        score: Score,
        itma_id: str,
        soundslice_folder_id: int | None,
    ) -> dict[str, Any]:
        embed_url = score.create_soundslice_slice(
            collection_metadata=None,  # title already on Score.title
            itma_id=itma_id,
            _folder_id=soundslice_folder_id,
        )
        return {"soundslice_iframe": embed_url}

    def _run_passthrough_aws_step(
        self,
        *,
        score: Score,
        itma_id: str,
        context: CollectionContext,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}

        # copy MusicXML file to S3 and record metadata
        out["musicxml"] = score.copy_musicxml_file_to_aws(
            collection_root=context.collection_root.parent
        )
        # copy incipit mp3s file to S3 and record metadata
        incipit_mp3_path = context.incipit_mp3_dir / f"{itma_id}.mp3"
        if incipit_mp3_path.exists():
            incipit_mp3_uri = copy_mp3_to_aws(
                str(incipit_mp3_path),
                collection_root=str(context.collection_root.parent),
            )
            if incipit_mp3_uri:
                out["incipit_audio"] = incipit_mp3_uri

        # copy performance mp3s file to S3 and record metadata
        performance_mp3_path = context.performance_mp3_dir / f"{itma_id}.mp3"
        if performance_mp3_path.exists():
            performance_mp3_uri = copy_mp3_to_aws(
                str(performance_mp3_path),
                collection_root=str(context.collection_root.parent),
            )
            if performance_mp3_uri:
                out["score_track_mp3"] = performance_mp3_uri

        # copy slow mp3s file to S3 and record metadata
        slow_mp3_path = context.slow_mp3_dir / f"{itma_id}.mp3"
        if slow_mp3_path.exists():
            slow_mp3_uri = copy_mp3_to_aws(
                str(slow_mp3_path),
                collection_root=str(context.collection_root.parent),
            )
            if slow_mp3_uri:
                out["score_track2_mp3"] = slow_mp3_uri

        return out


class CollectionProcessor:
    """Orchestrate collection-level data and metadata processing"""

    def __init__(self, score_processor: ScoreProcessor | None = None):
        self.score_processor = score_processor or ScoreProcessor()

    def run(
        self,
        *,
        collection_root: str | Path,
        selection: ScoreSelection,
        processing_steps: ScoreProcessingOrchestrator,
        metadata_csv_path: str | Path | None = None,
        save: bool = True,
    ) -> str | None:
        """Run collection processing, metadata updates, and create outputs."""
        context = CollectionContext(collection_root=Path(collection_root))
        score_paths = self._resolve_score_paths(
            context=context,
            selection=selection
        )

        # If user doesn't supply an input metadata CSV, we still may want to
        # build metadata iteratively via multiple one-off runs:
        # if a "_processed" metadata output file exists, load it and upsert
        # into it; otherwise start from an empty metadata table
        default_processed_out_path = (
                context.collection_root
                / f"{context.collection_root.name}_metadata_processed.csv"
        )

        if metadata_csv_path is not None:
            collection_metadata = CollectionMetadata(str(metadata_csv_path))
            collection_metadata.load_collection_metadata()
        else:
            if default_processed_out_path.exists():
                collection_metadata = CollectionMetadata(
                    str(default_processed_out_path)
                )
                collection_metadata.load_collection_metadata()
            else:
                collection_metadata = CollectionMetadata(None)
                collection_metadata.create_empty_metadata_table()

        title_lookup: dict[str, str] | None = None
        if (
                metadata_csv_path is not None and
                collection_metadata.metadata is not None
        ):
            title_lookup = {
                str(row.get("slug") or "").strip():
                    str(row.get("title") or "").strip()
                for _, row in collection_metadata.metadata.iterrows()
            }

            missing = [
                p.stem.strip()
                for p in score_paths
                if p.stem.strip() not in title_lookup
            ]
            if missing:
                preview = ", ".join(missing[:10])
                suffix = " ..." if len(missing) > 10 else ""
                raise KeyError(
                    f"Metadata is missing for score file(s): {preview}{suffix}"
                )

        soundslice_folder_id: int | None = None
        if processing_steps.mode in {
            ProcessingMode.SOUNDSLICE,
            ProcessingMode.ALL
        }:
            soundslice_folder_id = ensure_soundslice_folder_exists(
                context.collection_root.name
            )

        patches: dict[str, dict[str, Any]] = {}
        has_metadata = metadata_csv_path is not None

        # Processes scores sequentially or in parallel per 'processing_steps'
        # settings
        if not processing_steps.parallel:
            metadata_for_title = (
                _TitleLookupMetadata(title_lookup)
                if (has_metadata and title_lookup is not None)
                else None
            )

            for score_path in score_paths:
                itma_id = _get_itma_id_from_path(score_path)

                patch = self.score_processor.process_single_score(
                    score_path=score_path,
                    itma_id=itma_id,
                    context=context,
                    processing_steps=processing_steps,
                    collection_metadata=metadata_for_title,
                    soundslice_folder_id=soundslice_folder_id,
                    custom_title=None,
                    has_metadata=has_metadata,
                )
                for s, values in patch.items():
                    patches.setdefault(s, {}).update(values)

        else:
            with (ProcessPoolExecutor(max_workers=processing_steps.max_workers)
                  as ex):
                futures = [
                    ex.submit(
                        _single_score_score_worker,
                        score_path_str=str(p),
                        collection_root_str=str(context.collection_root),
                        mode=str(processing_steps.mode.value),
                        analysis_methods=processing_steps.analysis_methods,
                        derivative_methods=processing_steps.derivative_methods,
                        soundslice_folder_id=soundslice_folder_id,
                        title_lookup=title_lookup,
                        has_metadata=has_metadata,
                    )
                    for p in score_paths
                ]

                for fut in as_completed(futures):
                    patch = fut.result()
                    for s, values in patch.items():
                        patches.setdefault(s, {}).update(values)

        # Only write output CSV if we edited metadata content
        if not patches:
            return None

        if processing_steps.allow_new_rows:
            collection_metadata.upsert_row_updates(patches)
        else:
            collection_metadata.apply_row_updates(patches)

        if not save:
            return None

        if metadata_csv_path is None:
            out_path = (
                context.collection_root
                / f"{context.collection_root.name}_metadata_processed.csv"
            )
            return collection_metadata.save(output_path=str(out_path))

        return collection_metadata.save()

    def _resolve_score_paths(
        self,
        *,
        context: CollectionContext,
        selection: ScoreSelection,
    ) -> list[Path]:
        """Find and resolve score paths from user input paths."""
        xml_dir = context.xml_dir

        if selection.by == ScoreSelectionMode.ALL_FILES_IN_XML_DIR:
            if not xml_dir.exists():
                raise FileNotFoundError(
                    f"MusicXML directory does not exist: {xml_dir}"
                )

            xml_files = sorted(xml_dir.glob("*.xml"))
            musicxml_files = sorted(xml_dir.glob("*.musicxml"))
            score_paths = xml_files + musicxml_files
            if not score_paths:
                raise FileNotFoundError(
                    f"No .xml or .musicxml files found in: {xml_dir}"
                )
            return score_paths

        if selection.by == ScoreSelectionMode.SINGLE_FILE_BY_PATH:
            if selection.score_path is None:
                raise ValueError(
                    "Score selection by path mode requires user to enter a "
                    "valid path to a MusicXML score file."
                )
            p = selection.score_path
            if not p.exists():
                raise FileNotFoundError(
                    f"Score input path does not exist: {p}"
                )
            return [p]

        if selection.by == ScoreSelectionMode.SINGLE_FILE_BY_ITMA_ID:
            if not xml_dir.exists():
                raise FileNotFoundError(
                    f"MusicXML directory does not exist: {xml_dir}"
                )
            if not selection.itma_id:
                raise ValueError(
                    "Score selection by ITMA id requires user to provide a "
                    "valid unique ITMA identifier / 'slug' value."
                )

            itma_id = str(selection.itma_id).strip()
            if not itma_id:
                raise ValueError("ITMA id is blank.")

            p_xml = xml_dir / f"{itma_id}.xml"
            if p_xml.exists():
                return [p_xml]

            p_musicxml = xml_dir / f"{itma_id}.musicxml"
            if p_musicxml.exists():
                return [p_musicxml]

            raise FileNotFoundError(
                f"No score file found for ITMA id {itma_id!r} in {xml_dir}"
            )

        raise ValueError(f"Unknown ScoreSelection by: {selection.by!r}")
