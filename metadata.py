"""metadata.py holds ScoreMetadata and CollectionMetadata classes to
manage metadata corresponding respectively to Score and Collection objects.

Design goals:
- Strict schema enforcement for field NAMES:
    * extra/unrecognized columns in input CSV => hard error
    * missing columns in input CSV => allowed; will be added empty to match schema
- Canonical unique id column name in CSV: 'slug' (lowercase). 'Slug' is a hard error.
- Safe for batch processing and parallelism:
    * ScoreMetadata is a pure row-update builder (no pandas / no I/O)
    * CollectionMetadata is responsible for load/update/save
- Cross-platform (Windows/macOS) + Excel-friendly output via UTF-8 with BOM.
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
    Row-update builder for metadata of an individual score.

    Notes:
      - Uses human-friendly naming: itma_id, metadata.
      - Still outputs the standard row-update map shape:
            {itma_id: {field: value}}
        where itma_id corresponds to the schema's 'slug' value.
    """

    itma_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def set(self, field: str, value: Any) -> None:
        self.metadata[str(field)] = value

    def update(self, mapping: dict[str, Any]) -> None:
        self.metadata.update(dict(mapping or {}))

    def as_row_update(self) -> dict[str, dict[str, Any]]:
        itma_id = str(self.itma_id).strip()
        if not itma_id:
            raise ValueError("Unique identifier itma_id not provided.")
        return {itma_id: dict(self.metadata)}


class CollectionMetadata:
    """
    Collection metadata manager.

    Hard requirements:
      - Unique id column must be exactly 'slug' (lowercase). 'Slug' is invalid.
      - Extra columns not in METADATA_FIELDS => hard error.
      - Missing columns are allowed and will be added empty on load.
      - slug values must be non-empty and unique.

    Row-update rules:
      - PRESERVE_FIELDS cannot be updated (error).
      - Only OVERWRITE_FIELDS can be updated (error on any other field).
      - CONSTANTS are enforced for touched ids on every apply/upsert.
    """

    SLUG_COL = "slug"

    def __init__(self, metadata_path: str | os.PathLike[str] | None):
        self.metadata_path = str(metadata_path) if metadata_path is not None else None
        self.metadata: pd.DataFrame | None = None

    def load_collection_metadata(self) -> pd.DataFrame:
        if self.metadata_path is None:
            raise ValueError(
                "metadata_path is None. Use create_empty_metadata() if you want "
                "a new in-memory table without an input CSV."
            )

        encodings_to_try = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
        last_error: Exception | None = None
        df: pd.DataFrame | None = None

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

        df = self._validate_columns_strict_missing_ok(df)
        df = self._normalize_types(df)
        df = self._ensure_schema_columns(df)            # <-- add missing columns here
        df = self._normalize_and_validate_slug_values(df)

        self.metadata = df
        return df

    def create_empty_metadata(self) -> pd.DataFrame:
        cols = list(METADATA_FIELDS)
        df = pd.DataFrame(columns=cols).astype("string")
        df = df.set_index(self.SLUG_COL, drop=False)
        self.metadata = df
        return df

    def get_score_metadata(self, itma_id: str) -> dict[str, Any]:
        """
        Return a single row as dict by looking up itma_id (schema column: 'slug').
        """
        itma_id = str(itma_id).strip()
        if not itma_id:
            raise ValueError("Unique identifier itma_id not provided.")

        if self.metadata is None:
            raise RuntimeError(
                "Metadata not loaded. Call load_collection_metadata() or "
                "create_empty_metadata() first."
            )

        row = self.metadata.loc[itma_id]
        if isinstance(row, pd.DataFrame):
            raise ValueError(
                f"Multiple rows found for unique identifier {itma_id!r}. "
                "Only one row is allowed per unique identifier."
            )
        return row.to_dict()

    def apply_row_updates(self, row_updates: dict[str, dict[str, Any]]) -> None:
        """Apply row updates but require that all itma_id values already exist."""
        self._apply_row_updates_core(row_updates, allow_new_rows=False)

    def upsert_row_updates(self, row_updates: dict[str, dict[str, Any]]) -> None:
        """Apply row updates and create rows for missing itma_id values."""
        self._apply_row_updates_core(row_updates, allow_new_rows=True)

    def save(
        self,
        output_path: str | None = None,
        *,
        suffix: str = "_processed",
        index: bool = False,
        encoding: str = "utf-8-sig",
    ) -> str:
        if self.metadata is None:
            raise RuntimeError("Nothing to save. Load or create metadata first.")

        if output_path is None:
            if self.metadata_path is None:
                raise ValueError(
                    "output_path is None but metadata_path is also None. "
                    "Provide output_path explicitly when starting from scratch."
                )
            in_path = Path(self.metadata_path)
            out_path = in_path.with_name(f"{in_path.stem}{suffix}{in_path.suffix}")
            output_path = str(out_path)

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure saved CSV matches the schema (column set + order)
        df = self._ensure_schema_columns(self.metadata)
        df = df[list(METADATA_FIELDS)]

        self._atomic_to_csv(df, out_path, index=index, encoding=encoding)
        return str(out_path)

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------

    def _validate_columns_strict_missing_ok(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Strict about *extra* columns; permissive about *missing* columns.
        Still strictly requires the 'slug' column (lowercase).
        """
        given_fields = set(map(str, df.columns))
        expected_fields = set(METADATA_FIELDS)

        if "Slug" in given_fields and self.SLUG_COL not in given_fields:
            raise ValueError(
                "Metadata CSV column 'Slug' is invalid; schema requires 'slug' (lowercase). "
                "Did you mean 'slug' not 'Slug'?"
            )

        if self.SLUG_COL not in given_fields:
            hint = " Did you mean 'slug' not 'Slug'?" if "Slug" in given_fields else ""
            raise ValueError(f"Metadata CSV is missing required column '{self.SLUG_COL}'." + hint)

        extra = sorted(given_fields - expected_fields)
        if extra:
            raise ValueError(
                "Metadata CSV contains extra column(s) not in the schema: "
                f"{extra}"
            )

        # Missing columns are allowed (we will add them later)
        return df

    @staticmethod
    def _normalize_types(df: pd.DataFrame) -> pd.DataFrame:
        return df.astype("string")

    @staticmethod
    def _ensure_schema_columns(df: pd.DataFrame) -> pd.DataFrame:
        """
        Add any missing schema columns as empty (pd.NA) columns and ensure
        we have at least the full schema column set.
        """
        expected = list(METADATA_FIELDS)
        for c in expected:
            if c not in df.columns:
                df[c] = pd.NA
        return df

    def _normalize_and_validate_slug_values(self, df: pd.DataFrame) -> pd.DataFrame:
        slug_series = df[self.SLUG_COL].astype("string").str.strip()
        df[self.SLUG_COL] = slug_series

        invalid_mask = slug_series.isna() | (slug_series == "") | (slug_series.str.lower() == "nan")
        if bool(invalid_mask.any()):
            bad_rows = df.index[invalid_mask].tolist()[:10]
            raise ValueError(
                "Metadata contains blank/invalid slug value(s). "
                f"Sample row indices: {bad_rows}"
            )

        dup_mask = slug_series.duplicated(keep=False)
        if bool(dup_mask.any()):
            dup_slugs = slug_series[dup_mask].value_counts().index.tolist()[:10]
            raise ValueError(
                "Metadata contains duplicated slug value(s). "
                f"Sample duplicates: {dup_slugs}"
            )

        return df.set_index(self.SLUG_COL, drop=False)

    def _apply_row_updates_core(self, row_updates: dict[str, dict[str, Any]], *, allow_new_rows: bool) -> None:
        if not row_updates:
            return

        if self.metadata is None:
            raise RuntimeError(
                "Metadata not loaded. Call load_collection_metadata() or "
                "create_empty_metadata() first."
            )

        table = self.metadata

        normalized: dict[str, dict[str, Any]] = {}
        for raw_id, md in row_updates.items():
            itma_id = str(raw_id).strip()
            if not itma_id:
                raise ValueError("Row updates contain a blank itma_id key.")
            normalized[itma_id] = dict(md or {})

        ids = pd.Index(normalized.keys(), dtype="object")
        missing_ids = ids.difference(table.index)

        if len(missing_ids) > 0 and not allow_new_rows:
            preview = ", ".join(map(str, missing_ids[:10]))
            raise ValueError(
                "Row updates contain id(s) not present in metadata: "
                + preview
                + (" ..." if len(missing_ids) > 10 else "")
            )

        if len(missing_ids) > 0 and allow_new_rows:
            blank = pd.DataFrame(index=missing_ids, columns=table.columns).astype("string")
            blank[self.SLUG_COL] = blank.index.astype("string")
            table = pd.concat([table, blank], axis=0).sort_index()
            self.metadata = table

        update_df = pd.DataFrame.from_dict(normalized, orient="index").astype("string")

        attempted_preserve = (set(update_df.columns) & set(PRESERVE_FIELDS)) - {self.SLUG_COL}
        if attempted_preserve:
            raise ValueError(
                "Row updates attempted to update preserved field(s): "
                f"{sorted(attempted_preserve)}"
            )

        overwrite_cols = [c for c in update_df.columns if c in OVERWRITE_FIELDS]
        illegal_cols = [c for c in update_df.columns if c not in OVERWRITE_FIELDS]
        if illegal_cols:
            raise ValueError(
                "Row updates included field(s) not permitted by schema overwrite rules: "
                f"{sorted(illegal_cols)}"
            )

        # Ensure schema columns exist (should already after load/create/save normalization)
        table = self._ensure_schema_columns(table)

        if overwrite_cols:
            table.update(update_df[overwrite_cols])

        for field, value in CONSTANTS.items():
            table.loc[ids, field] = value

    @staticmethod
    def _atomic_to_csv(df: pd.DataFrame, output_path: Path, *, index: bool, encoding: str) -> None:
        fd, tmp_name = tempfile.mkstemp(
            prefix=output_path.name + ".",
            suffix=".tmp",
            dir=str(output_path.parent),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)

        try:
            df.to_csv(tmp_path, index=index, encoding=encoding, lineterminator="\n")
            os.replace(tmp_path, output_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
