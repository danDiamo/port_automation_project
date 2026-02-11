"""metadata.py holds ScoreMetadata and CollectionMetadata classes to
manage metadata corresponding respectively to Score and Collection objects.

Design goals:
- Strict schema enforcement for field names:
    * extra/unrecognized columns in input CSV => hard error
    * missing columns in input CSV => allowed; will be added in Dataframe to
    ensure that output CSVs match schema.
- Canonical unique id column name in CSV: 'slug' (lowercase). If this exact
field is not provided we throw a hard error.

- Safe for batch processing and parallelism:
    * ScoreMetadata is a row-update builder (no pandas / no I/O)
    * CollectionMetadata is responsible for load/update/save
- Cross-platform (Windows/macOS) & Excel-friendly output via UTF-8 with BOM.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from _metadata_schema import (
    CONSTANTS,
    METADATA_FIELDS,
    OVERWRITE_FIELDS,
    PRESERVE_FIELDS
)


@dataclass
class ScoreMetadata:
    """
    Represents the metadata associated with a single score, storing metadata
    fields in a dict with an ITMA unique identifier ('slug') as dict key.

    ScoreMetadata manages and manipulates score-related metadata.
    It is designed to support operations such as setting individual metadata
    fields, updating multiple fields from a dictionary, and preparing
    metadata for ingestion into a collection-level metadata table.
    """

    itma_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def set(self, field: str, value: Any) -> None:
        """
        Set a metadata field value.

        Args:
            field: The metadata field name.
            value: The value to set for the field.
        """
        self.metadata[str(field)] = value

    def update(self, mapping: dict[str, Any]) -> None:
        """
        Update metadata fields with values from a mapping.

        Args:
            mapping: A dictionary of field-value pairs to update.
        """
        self.metadata.update(dict(mapping or {}))

    def update_row(self) -> dict[str, dict[str, Any]]:
        """
        Create a row in a format compatible with a collection-level metadata
        table.

        Returns:
            A dictionary with the unique identifier as the key and metadata as
             the value.
        """
        itma_id = str(self.itma_id).strip()
        if not itma_id:
            raise ValueError("Unique identifier required. 'slug' field "
                             "cannot be empty.")
        return {itma_id: dict(self.metadata)}


class CollectionMetadata:
    """
    Models and manages collection-level metadata.

    Hard requirements:
      - Unique id column/field must be named 'slug' (lowercase).
      - Any columns passed from external CSV that aren't in
      METADATA_FIELDS will throw an error.
      - Missing METADATA_FIELDS columns are allowed. Such columns will be
      added on load to ensure that output CSVs match the schema, but they
      will remain empty unless filled during our metadata processing
      operations.
      - All entries in 'slug' column must be non-empty and unique.

    Row-update rules:
      - PRESERVE_FIELDS as defined in metadata schema cannot be updated. This
      subset of fields is passthrough only.
      - Only OVERWRITE_FIELDS can be updated.
      - CONSTANTS fields are populated with pre-defined static strings (as
      defined in metadata schema) on every apply/upsert operation.
    """
    
    # store our metadata key as a class constant
    KEY_COL = "slug"

    def __init__(self, metadata_path: str | os.PathLike[str] | None):
        """Initializes CollectionMetadata object with optional metadata path.

        Args:
            metadata_path (str | os.PathLike[str] | None): Path to metadata CSV
             file.
            If None, creates an empty in-memory table that we can populate
            row-wise as we process individual scores.
        """
        self.metadata_path = str(metadata_path) if metadata_path is not None\
            else None
        self.metadata: pd.DataFrame | None = None

    def load_collection_metadata(self) -> pd.DataFrame:
        """Loads, validates, and normalizes collection metadata"""
        if self.metadata_path is None:
            raise ValueError(
                "Metadata_path is None. To create a new in-memory "
                "Dataframe without an input CSV, use create_empty_metadata()."
            )

        # try to read various possible Excel output encodings
        encodings_to_try = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
        last_error: Exception | None = None
        df: pd.DataFrame | None = None
        
        # try to read CSV with multiple Windows-friewndly encodings in case 
        # of formatting issues relating to Excel usage in external metadata 
        # processing work 
        for enc in encodings_to_try:
            try:
                df = pd.read_csv(self.metadata_path, encoding=enc)
                break
            except UnicodeDecodeError as e:
                last_error = e

        if df is None:
            raise UnicodeDecodeError(
                "utf-8",
                b"",
                0,
                1,
                f"Could not decode CSV '{self.metadata_path}'. "
                f"Tried encodings: {encodings_to_try}. "
                f"Last error: {last_error}",
            )

        df = self._validate_columns(df)
        df = self._normalize_types(df)
        # add any missing cols. from chema
        df = self._add_missing_columns(df)
        df = self._normalize_and_validate_slug_values(df)

        self.metadata = df
        return df

    def create_empty_metadata_table(self) -> pd.DataFrame:
        """Creates an empty Dataframe that we can populate row-wise."""
        cols = list(METADATA_FIELDS)
        df = pd.DataFrame(columns=cols).astype("string")
        df = df.set_index(self.KEY_COL, drop=False)
        self.metadata = df
        return df

    def get_score_metadata(self, itma_id: str) -> dict[str, Any]:
        """
        Return a single metadata row (i.e. metadata record for a single
        score) as a dict by looking up unique id in 'slug' column.
        """
        itma_id = str(itma_id).strip()
        if not itma_id:
            raise ValueError("Unique identifier not provided for score "
                             f"{itma_id}.")

        if self.metadata is None:
            raise RuntimeError(
                f"Metadata not loaded for score {itma_id}. Call "
                "load_collection_metadata() or "
                "create_empty_metadata() first."
            )

        try:
            row = self.metadata.loc[itma_id]
        except KeyError as e:
            raise KeyError(
                f"No metadata record found for score {itma_id!r}.") from e

        if isinstance(row, pd.DataFrame):
            raise ValueError(
                f"Multiple metadata records found for unique "
                f"identifier {itma_id!r}. "
                "Only one metadata record is allowed per unique identifier."
            )
        return row.to_dict()

    def apply_row_updates(self, row_updates: dict[str, dict[str, Any]]) \
            -> None:
        """ Update existing rows in the collection-level metadata table."""
        self._apply_row_updates_core(row_updates, allow_new_rows=False)

    def upsert_row_updates(self, row_updates: dict[str, dict[str, Any]]) \
            -> None:
        """
        Add new rows to collection-level metadata table for scores that do not
        have unique ITMA id values in 'slug' col.

        This allows us to create metadata entries for MusicXML files
        that are not included in the input metadata file, or for cases in
        which no metadata CSV is provided and we're creating & populating
        the metadata from scratch.
        """
        self._apply_row_updates_core(row_updates, allow_new_rows=True)

    def save(
        self,
        output_path: str | None = None,
        *,
        suffix: str = "_processed",
        index: bool = False,
        encoding: str = "utf-8-sig",
    ) -> str:
        """
        Save collection-level metadata to CSV file.
        """
        if self.metadata is None:
            raise RuntimeError("No metadata avialable to save. Load or create "
                               "metadata first.")

        if output_path is None:
            if self.metadata_path is None:
                raise ValueError(
                    "No output_path or metadata_path provided. "
                    "Cannot infer output path."
                    "At least one of these paths must be provided by user."
                )
            in_path = Path(self.metadata_path)
            # automatically derive output path from input path if not
            # defined by user
            out_path = in_path.with_name(
                f"{in_path.stem}{suffix}{in_path.suffix}"
            )
            output_path = str(out_path)

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure saved CSV matches ITMA's metadata schema (field names &
        # order)
        df = self._add_missing_columns(self.metadata)
        df = df[list(METADATA_FIELDS)]

        self._atomic_to_csv(df, out_path, index=index, encoding=encoding)
        return str(out_path)

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------

    def _validate_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Validates input metadata against the ITMA metadata schema.

        Extra columns outside the scope of the schema are NOT permitted in
        input metadata files.
        Incomplete sets of columns are allowed; in such cases missing columns
        will be created and populated with empty values.

        Strictly requires the 'slug' column (lowercase) to be populated with
        unique id values.
        """
        given_fields = set(map(str, df.columns))
        expected_fields = set(METADATA_FIELDS)

        if self.KEY_COL not in given_fields:
            raise ValueError(
                f"Metadata CSV is missing required column '{self.KEY_COL}'."
            )

        extra_fields = sorted(given_fields - expected_fields)
        if extra_fields:
            raise ValueError(
                "Metadata CSV contains the following column(s) which are "
                "incompatible with the Port metadata schema: "
                f"{extra_fields}"
            )

        # Missing columns are allowed but they are populated via 
        # _add_missing_columns() (see below) rather than by this method.
        return df

    @staticmethod
    def _normalize_types(df: pd.DataFrame) -> pd.DataFrame:
        """Enforce string type for all metadata table content."""
        return df.astype("string")

    @staticmethod
    def _add_missing_columns(df: pd.DataFrame) -> pd.DataFrame:
        """
        Add any missing schema metadata fields as empty (pd.NA) columns.
        """
        expected_cols = list(METADATA_FIELDS)
        for col in expected_cols:
            if col not in df.columns:
                df[col] = pd.NA
        return df

    def _normalize_and_validate_slug_values(
            self, df: pd.DataFrame
    ) -> pd.DataFrame:
        """Normalize and validate ITMA id entries in 'slug' column"""
        # force str type
        slug_series = df[self.KEY_COL].astype("string").str.strip()
        df[self.KEY_COL] = slug_series
        # detect null entries
        invalid_mask = (
                slug_series.isna() |
                (slug_series == "") |
                (slug_series.str.lower()) == "nan"
        )
        if bool(invalid_mask.any()):
            bad_rows = df.index[invalid_mask].tolist()[:10]
            raise ValueError(
                "Metadata 'slug' column contains blank/invalid ITMA id "
                f"value(s). Sample row indices: {bad_rows}"
            )
        # detect duplicates
        dup_mask = slug_series.duplicated(keep=False)
        if bool(dup_mask.any()):
            dup_slugs = (
                slug_series[dup_mask].value_counts().index.tolist()
            )[:10]
            raise ValueError(
                "Metadata 'slug' column contains duplicated ITMA id value(s). "
                f"Sample duplicates: {dup_slugs}"
            )
        # return the dataframe less any invalid slug entries
        return df.set_index(self.KEY_COL, drop=False)

    def _apply_row_updates_core(self,
                                row_updates: dict[str, dict[str, Any]],
                                *,
                                allow_new_rows: bool)\
            -> None:
        """Apply row updates to collection-level metadata table"""
        if not row_updates:
            return

        if self.metadata is None:
            raise RuntimeError(
                "Metadata not loaded. Call load_collection_metadata() or "
                "create_empty_metadata() first."
            )

        metadata_table = self.metadata

        # type hint / create new_row dict to hold cleaned import content
        new_row_content: dict[str, dict[str, Any]] = {}
        # validate / clean up formatting of unique identifiers in raw row
        # metadata to be imported.
        # Populate 'new_row' with the resultant clean import row content.
        for score_id, score_metadata in row_updates.items():
            validated_itma_id = str(score_id).strip()
            if not validated_itma_id:
                raise ValueError("Row updates must contain a unique "
                                 "identifier key.")
            new_row_content[validated_itma_id] = dict(score_metadata or {})

        ids = pd.Index(new_row_content.keys(), dtype="object")
        missing_ids = ids.difference(metadata_table.index)
        # apply row updates strictly: update existing rows and don't
        # create any new ones. Fail if the import includes
        # unique identifiers that are not already present in the metadata
        # table.
        if len(missing_ids) > 0 and not allow_new_rows:
            preview = ", ".join(map(str, missing_ids[:10]))
            raise ValueError(
                "Row updates contain id(s) not present in metadata: "
                + preview
                + (" ..." if len(missing_ids) > 10 else "")
            )
        # apply the row updates ('upsert'). Creates blank rows
        # for scores that don't already exist in the metadata table and
        # updates them as metadata ouput values are created.
        if len(missing_ids) > 0 and allow_new_rows:
            blank = pd.DataFrame(
                index=missing_ids,
                columns=metadata_table.columns
            ).astype("string")

            blank[self.KEY_COL] = blank.index.astype("string")
            table = pd.concat([metadata_table, blank], axis=0).sort_index()
            self.metadata = table
            metadata_table = table
        # populate the new blank row with metadata
        update_df = pd.DataFrame.from_dict(
            new_row_content,
            orient="index"
        ).astype("string")

        # explicitly allow edits to OVERWRITE_FIELDS metadata fields only.
        overwrite_cols = \
            [c for c in update_df.columns if c in OVERWRITE_FIELDS]
        illegal_cols = \
            [c for c in update_df.columns if c not in OVERWRITE_FIELDS]
        if illegal_cols:
            raise ValueError(
                "Row updates included field(s) not permitted by schema "
                f"overwrite rules: {sorted(illegal_cols)}"
            )

        # Ensure schema columns exist
        # (should already after load/create/save normalization)
        metadata_table = self._add_missing_columns(metadata_table)
        # update collection-level metadata table
        if overwrite_cols:
            metadata_table.update(update_df[overwrite_cols])
        # Fill CONTSTANTS fields default values as defined in metadata schema
        for field, value in CONSTANTS.items():
            metadata_table.loc[ids, field] = value

    @staticmethod
    def _atomic_to_csv(
            df: pd.DataFrame,
            output_path: Path,
            *,
            index: bool,
            encoding: str
    ) -> None:
        """
        Write DataFrame to CSV file atomically, ensuring data integrity during
         the write operation (safeguards against partial writes or data loss).

        We first write to tmp file then replace the target file
        when write operation completes successfully.
        """

        file_descriptor, tmp_name = tempfile.mkstemp(
            prefix=output_path.name + ".",
            suffix=".tmp",
            dir=str(output_path.parent),
        )
        os.close(file_descriptor)
        tmp_path = Path(tmp_name)

        try:
            df.to_csv(
                tmp_path, index=index, encoding=encoding, lineterminator="\n"
            )
            os.replace(tmp_path, output_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
