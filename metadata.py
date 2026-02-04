"""metadata.py holds ScoreMetadata and CollectionMetadata classes to
manage metadata corresponding respectively to Score and Collection objects"""

# TODO: Tidy up & inspect apply_patches() ; Tests for everything in this file

import warnings
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# global constant: Port metadata schema.
# TODO: Talk through with ITMA.
# TODO: Possibly replace with union of subsets below
METADATA_FIELDS = {
    'Title',  # provided by ITMA
    'alternative_title',  # Provided by ITMA
    'Composer',  # provided by ITMA
    'Featured_image',  # TODO: Populate with AWS path to the incipit svg
    # file, as returned by Score.convert_incipit_to_svg
    'Image_alt_text',  # TODO: Populate. Content is always
    # 'Musical Notation' string
    'Summary',  # Provided by ITMA: 'from [Collection name]'
    'Main_textbox',  # provided by ITMA
    'Soundslice_iframe',  # TODO: populate embed link for Soundslice iframe
    # Note: first we need to add Soundslice integration to the Score class
    'score_track_title',  # Provided by ITMA (catalogue title field)
    'score_track_mp3',  # Provided by ITMA. AWS path to performance mp3
    # file, if provided.
    'score_track_rights',  # TODO: Populate. Content is always
    # 'In Copyright' string
    'score_track_catalog_url',  # Provided by ITMA, online catalogue link
    'score_track2_title',  # Provided by ITMA (slow recording title field)
    'score_track2_mp3',  # # Provided by ITMA (path to slow recording mp3
    # file on AWS)
    'score_track2_rights',  # TODO: Populate. Content is always
    # 'In Copyright' string
    'score_track2_catalog_url',  # Provided by ITMA, online catalogue link
    'Video_url',  # Provided by ITMA, Youtube embed code
    'Video_title',  # Provided by ITMA (catalogue title field)
    'video_catalog_url',  # Provided by ITMA
    'PDFDownload',  # TODO: Populate with AWS path to score PDF file,
    # as returned by Score.convert_score_to_pdf()
    'Related_entries',  # Provided by ITMA
    'Tune_type',  # Provided by ITMA.
    'Explore_tag',  # May not be included?
    'collection_tag'  # TODO: Populate. Content is always 'Port' string
    'Slug',  # Unique identifier field. Provided by ITMA.
    'Federated_search_term',  # Duplicate content from 'Title' field in
    # this field.
    'key_signature',  # TODO: populate from Score.extract_key_signature.
    # Output format is subject to change
    'mode'  # TODO: populate from Score.extract_mode_from_key_signature.
    # Output format is subject to change
    'tonic',
    # TODO: populate from Score.extract_tonic_from_key_signature.
    # Output format is subject to change
    'time_signature'  # TODO: Populate from Score.extract_time_signature
    'number_of_parts',  # TODO: Populate from Score.count_number_of_parts
    'abc_notation',  # TODO: Populate from Score.convert_score_to_abc
    # output stored in Score.abc attribute.
    'BB_code'  # TODO: populate from Score.create_breathnach_codes
    # Output format is subject to change
    'midi_audio_full',  # TODO: populate with path to MIDI audio file,
    # as returned by Score.write_score_to_midi
    'incipit_audio',  # TODO: populate with path to mp3 file, , as returned by
    # Score.convert_incipit_to_mp3
    'musicXML',  # TODO: AWS path to MusicXML file, as returned by
    # Score.copy_musicxml_file_to_aws
}

PRESERVE_FIELDS = {
    "Slug",
    "Title",
    "alternative_title",
    "Composer",
    "Summary",
    "Main_textbox",
    "score_track_title",
    "score_track_mp3",
    "score_track_catalog_url",
    "score_track2_title",
    "score_track2_mp3",
    "score_track2_catalog_url",
    "Video_url",
    "Video_title",
    "video_catalog_url",
    "Related_entries",
    "Tune_type",
    "Explore_tag",
}

OVERWRITE_FIELDS = {
    # pipeline-enforced constants
    "Image_alt_text",
    "collection_tag",
    "score_track_rights",
    "score_track2_rights",

    # pipeline-generated assets / embeds
    "Featured_image",
    "PDFDownload",
    "Soundslice_iframe",
    "midi_audio_full",
    "incipit_audio",
    "musicXML",

    # pipeline-derived analysis / representations
    "Federated_search_term",
    "key_signature",
    "mode",
    "tonic",
    "time_signature",
    "number_of_parts",
    "abc_notation",
    "BB_code",
}

CONSTANTS = {
    "Image_alt_text": "Musical Notation",
    "collection_tag": "Port",
    "score_track_rights": "In Copyright",
    "score_track2_rights": "In Copyright",
}


@dataclass
class ScoreMetadata:

    """
    Patch-builder to update metadata for individual scores.

    Safe for parallelism: this object is plain-Python data and can be returned
    from worker processes. Use CollectionMetadata.apply_patches() to apply.
    """

    # type hinting
    slug: str
    values: dict[str, Any] = field(default_factory=dict)

    def set(self, field: str, value: Any) -> None:
        self.values[field] = value

    def update(self, mapping: dict[str, Any]) -> None:
        self.values.update(mapping)

    def as_patch(self) -> dict[str, dict[str, Any]]:
        """Formats ScoreMetadata as a patch dict"""
        slug = str(self.slug).strip()
        if not slug:
            raise ValueError("Unique identifier slug not provided.")
        return {slug: dict(self.values)}


class CollectionMetadata:
    """This class models and manages Collection metadata according to
    ITMA's Port metadata schema."""

    def __init__(self, metadata_path):
        self.metadata_path = metadata_path
        self.metadata = None

    def validate_schema(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Core: validate our sole hard requirement: each row must have
        a unique Slug value.

        Behavior:
          - If "Slug" column is missing: raise error (cannot proceed).
          - If some rows have null/blank Slug: warn and drop those rows.
          - If any remaining Slug values are duplicates: raise error.
          - If we have missing or extra columns vs our METADATA_FIELDS schema:
          warn only and proceed.

        Returns:
            A cleaned DataFrame.
        """
        if "Slug" not in df.columns:
            raise ValueError(
                f"Metadata file is missing required unique id 'Slug' column: "
                f"{self.metadata_path}"
            )

        expected_fields = set(METADATA_FIELDS)
        given_fields = set(df.columns)

        missing = sorted(expected_fields - given_fields)
        if missing:
            warnings.warn(
                "Metadata CSV is missing column(s). "
                "This is permitted, but some pipeline "
                f"outputs may not be written: {missing}",
                UserWarning,
            )

        extra = sorted(given_fields - expected_fields)
        if extra:
            warnings.warn(
                f"Metadata CSV contains extra column(s) not in schema. "
                f"This is permitted and they will be preserved: {extra}",
                UserWarning,
            )

        # Tidy up slug type / formatting
        slug_norm = df["Slug"].astype(str).str.strip()

        # Identify bad/empty slugs and drop those rows (warn-only)
        invalid_mask = (
                slug_norm.isna() |
                (slug_norm == "") |
                (slug_norm.str.lower() == "nan")
        )

        # identify and warn about bad slugs but don't throw an error
        if bool(invalid_mask.any()):
            bad_rows = df.index[invalid_mask].tolist()
            preview = bad_rows[:10]
            warnings.warn(
                "Metadata contains row(s) with blank/invalid Slug. "
                "These rows will be skipped. "
                f"Sample rows: {preview}"
                + (" ..." if len(bad_rows) > 10 else ""),
                UserWarning,
            )
            df = df.loc[~invalid_mask].copy()
            slug_norm = slug_norm.loc[~invalid_mask]

        # Require unique slugs in all remaining rows
        dup_mask = slug_norm.duplicated(keep=False)
        if bool(dup_mask.any()):
            dup_slugs = slug_norm[dup_mask].value_counts().index.tolist()[:10]
            raise ValueError(
                "Metadata contains duplicated Slug value(s). "
                f"Sample rows: {dup_slugs}"
            )

        return df

    def load_collection_metadata(self) -> pd.DataFrame:
        self.metadata = pd.read_csv(self.metadata_path)

        # Validate & drop bad rows
        self.metadata = self.validate_schema(self.metadata)

        # Normalize slug column formatting
        self.metadata["Slug"] = self.metadata["Slug"].astype(str).str.strip()

        # Index by slug for better performance
        self.metadata = self.metadata.set_index("Slug", drop=False)

        return self.metadata

    def get_score_metadata(self, slug: str) -> dict:
        """
        Return a single metadata row (as a dict) by looking up slug.

        Raises:
            ValueError if slug is missing, not found, or not unique.
        """

        if not slug or not str(slug).strip():
            raise ValueError("Unique identifier slug not provided.")

        if self.metadata is None:
            self.load_collection_metadata()

        slug = str(slug).strip()

        try:
            score_metadata = self.metadata.loc[slug]
        except KeyError as e:
            raise ValueError(
                f"No metadata found for ITMA item '{slug}'."
            ) from e

        # If slug is not unique:
        if isinstance(score_metadata, pd.DataFrame):
            raise ValueError(
                f"Multiple items found for unique identifier '{slug}'. "
                "Only one row is allowed per unique identifier."
            )

        return score_metadata.to_dict()

    def apply_patches(
            self,
            patches: dict[str, dict[str, Any]],
            *,
            warn_on_ignored_fields: bool = True,
    ) -> None:
        """
        Apply batch Score-level metadata updates to the Collection-level
        in-memory metadata table.

        Rules:
          - Slugs must already exist in the metadata table (hard requirement).
          - PRESERVE_FIELDS cannot be updated (error).
          - Only OVERWRITE_FIELDS can be updated.
          - CONSTANTS are enforced for all slugs in the patch batch.

        Args:
            patches: {slug: {field: value}}
        """

        if not patches:
            return

        if self.metadata is None:
            self.load_collection_metadata()

        metadata = self.metadata
        assert metadata is not None

        # Normalize patch slugs and keep "last write wins"
        # if duplicates appear in input
        normalized_patches: dict[str, dict[str, Any]] = {}
        for raw_slug, values in patches.items():
            slug = str(raw_slug).strip()
            if not slug:
                warnings.warn(
                    "apply_patches() received a blank slug identifier; "
                    "skipping this patch entry.",
                    UserWarning,
                )
                continue
            normalized_patches[slug] = dict(values or {})

        if not normalized_patches:
            return

        patch_slugs = pd.Index(normalized_patches.keys(), dtype="object")
        missing_slugs = patch_slugs.difference(metadata.index)
        if len(missing_slugs) > 0:
            preview = ", ".join(map(str, missing_slugs[:10]))
            raise ValueError(
                "Patch contains slug(s) not present in metadata CSV: "
                + preview
                + (" ..." if len(missing_slugs) > 10 else "")
            )

        patch_df = pd.DataFrame.from_dict(normalized_patches, orient="index")

        # Block edits to preserved fields
        # (except "Slug" itself, which we also don't want to write)
        attempted_preserve = (set(patch_df.columns) & set(PRESERVE_FIELDS)) - {
            "Slug"}
        if attempted_preserve:
            raise ValueError(
                "Patches attempted to update preserved field(s): "
                f"{sorted(attempted_preserve)}"
            )

        # Only apply OVERWRITE_FIELDS; ignore anything else
        # (typos, non-standard fields, etc.)
        overwrite_cols = [c for c in patch_df.columns if c in OVERWRITE_FIELDS]
        ignored_cols = [c for c in patch_df.columns if
                        c not in OVERWRITE_FIELDS]
        if ignored_cols and warn_on_ignored_fields:
            warnings.warn(
                "Ignoring patch field(s) that are not in OVERWRITE_FIELDS: "
                f"{sorted(ignored_cols)}",
                UserWarning,
            )

        # Ensure overwrite columns exist (we won't create new columns)
        for c in overwrite_cols:
            if c not in metadata.columns:
                metadata[c] = pd.NA

        # Apply the actual updates (index-aligned for performance)
        if overwrite_cols:
            metadata.update(patch_df[overwrite_cols])

        # Enforce constants for all slugs in this batch
        if CONSTANTS:
            for field, value in CONSTANTS.items():
                if field not in metadata.columns:
                    metadata[field] = pd.NA
                metadata.loc[patch_slugs, field] = value

        # Keep federated search term as a duplicate of Title
        # (if both columns exist)
        if "Federated_search_term" in metadata.columns and "Title" in metadata.columns:
            metadata.loc[patch_slugs, "Federated_search_term"] = metadata.loc[
                patch_slugs, "Title"]
        elif (
                "Federated_search_term" in metadata.columns
                and "Title" not in metadata.columns
        ):
            warnings.warn(
                "Cannot populate Federated_search_term because "
                "Title column is missing.",
                UserWarning,
            )

    def save(
            self,
            output_path: str | None = None,
            *,
            suffix: str = "_processed",
            index: bool = False,
    ) -> str:
        # TODO: check out path with ITMA & if this should also save to AWS
        """
        Save the in-memory metadata table back to CSV.

        Default behavior:
          - Write to a new file alongside the input file, named:
              <input_stem><suffix><input_suffix>
            e.g. "metadata.csv" -> "metadata_processed.csv"

        Args:
            output_path: Destination path. If None, derive from input name.
            suffix: string appended to the input filename when output_path is
            None.
            index: Whether to write the DataFrame index to CSV (default False).
                   Keep this False to avoid duplicating Slug.

        Returns:
            Path written to (as str).
        """

        if self.metadata is None:
            self.load_collection_metadata()

        df = self.metadata
        assert df is not None

        if output_path is None:
            from pathlib import Path

            in_path = Path(self.metadata_path)
            out_path = in_path.with_name(
                f"{in_path.stem}{suffix}{in_path.suffix}")
            output_path = str(out_path)

        df.to_csv(output_path, index=index)
        return output_path