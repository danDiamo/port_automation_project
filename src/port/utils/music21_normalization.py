"""
music21_normalization.py

Utilities for normalizing music21 score streams for export to LilyPond.

Handles:
- Pickup/anacrusis detection and alignment
- Barline preservation (repeats, double bars, finals)
- Volta/repeat bracket preservation
- Time signature and clef/key context management

Note: This module is not currently unit tested directly but is used by tests in
test_score.py.
"""

from __future__ import annotations

import copy
import warnings
from dataclasses import dataclass

import music21
from music21 import bar, clef, key, meter, layout, spanner

# ============================================================================
# Configuration Constants
# ============================================================================

# quarterLength tolerance for "ncomplete bar detection
EPS_Q = 1e-6

# Small negative offsets to keep grace notes ordered before their anchor notes
GRACE_ANCHOR_EPS = 1e-4

# Default number of bars per line in PDF layout
MEASURES_PER_LINE = 4


# ============================================================================
# Helper Functions
# ============================================================================

def _active_time_signature(
        bar: music21.stream.Measure,
        *,
        default_time_sig: meter.TimeSignature,
) -> meter.TimeSignature:
    """
    Resolve the time signature for `bar`.

    Prefer an explicit time signature, otherwise use Music21 context lookup,
    and finally fall back to the project's default time signature.

    Args:
        bar: The measure to query
        default_time_sig: Fallback time signature

    Returns:
        Active time signature for the measure
    """
    return (
            bar.timeSignature
            or bar.getContextByClass(meter.TimeSignature)
            or default_time_sig
    )


def _is_incomplete_bar(
        bar: music21.stream.Measure,
        *,
        default_time_sig: meter.TimeSignature,
) -> bool:
    """
    True if the bar is shorter than the expected bar duration under the
    active time signature. This is our pickup/anacrusis heuristic.

    Args:
        bar: The measure to check
        default_time_sig: Fallback time signature

    Returns:
        True if bar is incomplete (shorter than expected duration)
    """
    ts = _active_time_signature(bar, default_time_sig=default_time_sig)
    expected = float(ts.barDuration.quarterLength)
    actual = float(bar.duration.quarterLength)
    return actual < (expected - EPS_Q)


def _barline_type_string(raw_barline: bar.Barline | None) -> str | None:
    """
    Return a barline type string if present, otherwise None.

    We preserve whatever barline types (i.e. repeat markers, double & final
    barlines) and pass through to the PDF.

    Args:
        raw_barline: The barline to extract type from

    Returns:
        Barline type string or None
    """
    if raw_barline is None:
        return None
    t = getattr(raw_barline, "type", None)
    if t is None:
        return None
    s = str(t).strip()
    return s or None


# ============================================================================
# Barline Preservation
# ============================================================================

@dataclass(frozen=True)
class _BarlineSnapshot:
    """
    Minimal snapshot of a barline that is safe to re-apply after
    makeMeasures().

    For non-repeat barlines, only `barline_type` is used.
    For repeat barlines, `repeat_direction` and optional `repeat_times`
    are used.

    We keep this intentionally small and "preserve-only":
      - No inference of missing repeat direction.
      - No attempt to validate repeat pairing.
    """

    barline_type: str | None
    repeat_direction: str | None = None
    repeat_times: int | None = None


def _snapshot_barline(
        raw_barline: bar.Barline | None) -> _BarlineSnapshot | None:
    """
    Snapshot a single barline, preserving repeat direction/times when present.

    Note:
        In music21, repeat barlines often have .type values like 'heavy-light'
        or 'light-heavy'. Do not rely on barline.type == 'repeat' to detect
        repeat markers. Use isinstance(raw_barline, bar.Repeat) instead.

    Args:
        raw_barline: The barline to snapshot

    Returns:
        Barline snapshot or None
    """
    if raw_barline is None:
        return None

    barline_type = _barline_type_string(raw_barline)

    # Prefer explicit repeat objects (music21.bar.Repeat) when present.
    if isinstance(raw_barline, bar.Repeat):
        direction = getattr(raw_barline, "direction", None)
        times = getattr(raw_barline, "times", None)

        direction_clean = str(
            direction).strip() if direction is not None else None
        times_int = int(times) if isinstance(times, int) else None

        return _BarlineSnapshot(
            # Store the visual barline style for completeness, but re-encoding
            # is driven by repeat_direction.
            barline_type=barline_type,
            repeat_direction=direction_clean or None,
            repeat_times=times_int,
        )

    # If it's not a bar.Repeat, preserve the plain barline type.
    return _BarlineSnapshot(barline_type=barline_type)


def _find_embedded_repeat_snapshot(
        raw_bar: music21.stream.Measure,
        *,
        side: str,
) -> _BarlineSnapshot | None:
    """
    Look for repeat markers encoded *as elements inside the measure*.

    Preserve-only:
      - We only return a snapshot when repeat direction is available.
      - We do not infer direction from barline type or bar position.

    Args:
        raw_bar: The measure to search
        side: "left" or "right"

    Returns:
        Barline snapshot or None
    """
    side_clean = str(side).strip().lower()
    if side_clean not in {"left", "right"}:
        return None

    want_direction = "start" if side_clean == "left" else "end"

    for rep in raw_bar.getElementsByClass(bar.Repeat):
        snap = _snapshot_barline(rep)
        if snap is None:
            continue
        if snap.repeat_direction == want_direction:
            return snap

    return None


def _snapshot_barlines_by_bar_idx(
        *,
        raw_bars: list[music21.stream.Measure],
        start_bar_idx: int,
) -> list[tuple[_BarlineSnapshot | None, _BarlineSnapshot | None]]:
    """
    Snapshot left/right barlines by bar index so we can re-apply them after
    makeMeasures().

    Sources we consider (in order):
      1) Measure.leftBarline / Measure.rightBarline
      2) Embedded bar.Repeat elements inside the measure (fallback)

    Args:
        raw_bars: List of measures from the original part
        start_bar_idx: Index of first bar to keep (after pickup stripping)

    Returns:
        List of (left_snapshot, right_snapshot) tuples
    """
    barline_by_bar_idx: list[
        tuple[_BarlineSnapshot | None, _BarlineSnapshot | None]
    ] = []

    for raw_bar in raw_bars[start_bar_idx:]:
        left_snap = _snapshot_barline(getattr(raw_bar, "leftBarline", None))
        right_snap = _snapshot_barline(getattr(raw_bar, "rightBarline", None))

        # If repeat direction is missing, look for embedded Repeat objects.
        if left_snap is None or (left_snap.repeat_direction is None):
            embedded_left = _find_embedded_repeat_snapshot(
                raw_bar, side="left"
            )
            left_snap = embedded_left or left_snap

        if right_snap is None or (right_snap.repeat_direction is None):
            embedded_right = _find_embedded_repeat_snapshot(
                raw_bar, side="right"
            )
            right_snap = embedded_right or right_snap

        barline_by_bar_idx.append((left_snap, right_snap))

    return barline_by_bar_idx


def _apply_barlines_by_bar_idx(
        *,
        clean_bars: list[music21.stream.Measure],
        barline_by_bar_idx: list[
            tuple[_BarlineSnapshot | None, _BarlineSnapshot | None]],
) -> None:
    """
    Re-apply left/right barlines by index, preserving repeat direction/times
    when available.

    Args:
        clean_bars: The rebuilt measures
        barline_by_bar_idx: Barline snapshots to apply
    """

    def _to_barline(snapshot: _BarlineSnapshot | None) -> bar.Barline | None:
        if snapshot is None:
            return None

        # If we have repeat direction, rebuild a repeat barline.
        if snapshot.repeat_direction:
            rep = bar.Repeat()
            rep.direction = snapshot.repeat_direction
            if snapshot.repeat_times is not None:
                rep.times = snapshot.repeat_times
            return rep

        if snapshot.barline_type:
            return bar.Barline(snapshot.barline_type)

        return None

    # Assign left and right barlines to clean bars from snapshots.
    for clean_bar, (lb_snap, rb_snap) in zip(
            clean_bars,
            barline_by_bar_idx,
            strict=False,
    ):
        lb = _to_barline(lb_snap)
        rb = _to_barline(rb_snap)

        if lb is not None:
            clean_bar.leftBarline = lb
        if rb is not None:
            clean_bar.rightBarline = rb


# ============================================================================
# Pickup/Anacrusis Handling
# ============================================================================

def _pickup_end_offset_if_pickup(
        *,
        raw_bars: list[music21.stream.Measure],
        default_time_sig: meter.TimeSignature,
) -> float | None:
    """
    Return the end offset (quarterLength) of the pickup bar if the part starts
    with a pickup, otherwise None.

    Pickup detection rule:
      - The first Measure is a pickup if it is shorter than the expected
        barDuration under the active time signature.

    Args:
        raw_bars: List of measures to check
        default_time_sig: Fallback time signature

    Returns:
        End offset of pickup bar, or None if no pickup
    """
    if not raw_bars:
        return None

    first_bar = raw_bars[0]
    if not _is_incomplete_bar(first_bar, default_time_sig=default_time_sig):
        return None

    return float(first_bar.offset + first_bar.duration.quarterLength)


def _start_bar_idx_after_cutoff(
        *,
        raw_bars: list[music21.stream.Measure],
        cutoff_offset: float,
) -> int:
    """
    Return the first bar index that should be kept after applying a global
    pickup cutoff.

    Policy:
      - Bars that end at or before cutoff_offset are treated as pickup material
        and are dropped.
      - The first kept bar is the first one whose end time is strictly after
        cutoff_offset.

    This supports the score-level rule:
      - If ANY part has a pickup, we align ALL parts by shifting the first
        post-pickup downbeat to offset 0.0.

    Args:
        raw_bars: List of measures
        cutoff_offset: Offset threshold (in quarterLength)

    Returns:
        Index of first bar to keep
    """
    if cutoff_offset <= 0.0:
        return 0

    for idx, b in enumerate(raw_bars):
        bar_end = float(b.offset + b.duration.quarterLength)
        if bar_end > (cutoff_offset + EPS_Q):
            return idx

    return len(raw_bars)


# ============================================================================
# Content Copying (Time Signatures, Clefs, Keys, Notes)
# ============================================================================

def _copy_timesigs_to_clean_part(
        *,
        clean_part: music21.stream.Part,
        time_sigs: list[meter.TimeSignature],
        cutoff_offset: float,
        default_time_sig: meter.TimeSignature,
) -> None:
    """
    Copy time signatures into clean_part, applying the global cutoff shift.

    When cutoff_offset > 0:
      - Insert the time signature active at cutoff_offset at offset 0.0.
      - Copy later time signatures, shifting offsets left by cutoff_offset.
      - Drop time signatures that occur strictly before cutoff_offset.

    This ensures makeMeasures() has correct context at the new start.

    Args:
        clean_part: The part to populate
        time_sigs: Time signatures from the original part
        cutoff_offset: Offset threshold (in quarterLength)
        default_time_sig: Fallback time signature
    """
    if not time_sigs:
        clean_part.insert(0.0, copy.deepcopy(default_time_sig))
        return

    time_sigs_sorted = sorted(time_sigs, key=lambda t: float(t.offset))

    if cutoff_offset > 0.0:
        active_ts: meter.TimeSignature | None = None
        for ts in time_sigs_sorted:
            if float(ts.offset) <= cutoff_offset + EPS_Q:
                active_ts = ts
            else:
                break

        clean_part.insert(
            0.0,
            copy.deepcopy(
                active_ts if active_ts is not None else time_sigs_sorted[0]
            ),
        )

    for ts in time_sigs_sorted:
        ts_off = float(ts.offset)
        if cutoff_offset > 0.0 and ts_off < cutoff_offset - EPS_Q:
            continue
        clean_part.insert(max(0.0, ts_off - cutoff_offset), copy.deepcopy(ts))


def _copy_context_to_clean_part(
        *,
        raw_part: music21.stream.Part,
        clean_part: music21.stream.Part,
        cutoff_offset: float,
) -> None:
    """
    Copy clefs and key signatures into clean_part, applying the global cutoff
    shift.

    When cutoff_offset > 0:
      - Insert the clef/key active at cutoff_offset at offset 0.0 (if present).
      - Copy later clefs/keys, shifting offsets left by cutoff_offset.
      - Drop clefs/keys that occur strictly before cutoff_offset.

    Args:
        raw_part: Source part
        clean_part: Destination part
        cutoff_offset: Offset threshold (in quarterLength)
    """
    for cls in (clef.Clef, key.KeySignature):
        els = list(raw_part.recurse().getElementsByClass(cls))
        if not els:
            continue

        els_sorted = sorted(els, key=lambda e: float(e.offset))

        if cutoff_offset > 0.0:
            active_el = None
            for el in els_sorted:
                if float(el.offset) <= cutoff_offset + EPS_Q:
                    active_el = el
                else:
                    break
            if active_el is not None:
                clean_part.insert(0.0, copy.deepcopy(active_el))

        for el in els_sorted:
            el_off = float(el.offset)
            if cutoff_offset > 0.0 and el_off < cutoff_offset - EPS_Q:
                continue
            clean_part.insert(max(0.0, el_off - cutoff_offset),
                              copy.deepcopy(el))


def _copy_notes_and_rests_to_clean_part(
        *,
        raw_part: music21.stream.Part,
        clean_part: music21.stream.Part,
        cutoff_offset: float,
) -> None:
    """
    Copy notes/rests into clean_part, adjusting offsets and preserving
    grace-note ordering.

    Any content before cutoff_offset is dropped. Remaining content is
    shifted so cutoff_offset becomes 0.0.

    Args:
        raw_part: Source part
        clean_part: Destination part
        cutoff_offset: Offset threshold (in quarterLength)
    """
    pending_graces: list[music21.note.Note] = []

    for el in raw_part.flatten().notesAndRests:
        dur = getattr(el, "duration", None)
        is_grace = bool(dur is not None and getattr(dur, "isGrace", False))

        if is_grace and isinstance(el, music21.note.Note):
            pending_graces.append(copy.deepcopy(el))
            continue

        anchor_offset = float(el.offset)

        if pending_graces:
            # Insert grace notes immediately before the next non-grace event.
            for i, gn in enumerate(pending_graces):
                back = GRACE_ANCHOR_EPS * (len(pending_graces) - i)
                ins = max(0.0, anchor_offset - back)
                if ins >= cutoff_offset - EPS_Q:
                    clean_part.insert(max(0.0, ins - cutoff_offset), gn)
            pending_graces.clear()

        if anchor_offset < cutoff_offset - EPS_Q:
            continue

        clean_part.insert(
            max(0.0, anchor_offset - cutoff_offset),
            copy.deepcopy(el),
        )


# ============================================================================
# Volta/Repeat Bracket Preservation
# ============================================================================

def _snapshot_voltas_by_bar_idx(
        *,
        raw_part: music21.stream.Part,
        raw_bars: list[music21.stream.Measure],
        start_bar_idx: int,
) -> list[tuple[list[int] | str | None, int, int]]:
    """
    Snapshot voltas (RepeatBracket spanners) by bar index.

    Returns:
        List of tuples: (number, start_idx, end_idx) where indices refer to the
        bar list AFTER pickup stripping is applied (i.e., relative to
        raw_bars[start_bar_idx:]).

    Args:
        raw_part: Source part
        raw_bars: List of measures from the original part
        start_bar_idx: Index of first bar to keep (after pickup stripping)

    Returns:
        List of (number, start_idx, end_idx) tuples
    """
    raw_voltas: list[tuple[list[int] | str | None, int, int]] = []

    for raw_volta in raw_part.recurse().getElementsByClass(
            spanner.RepeatBracket):
        getter = getattr(raw_volta, "getSpannedElements", None)
        if not callable(getter):
            continue

        spanned = list(getter())
        if not spanned:
            continue

        # Map spanned bars back to indices in raw_bars.
        idxs: list[int] = []
        for el in spanned:
            try:
                idxs.append(raw_bars.index(el))
            except ValueError:
                continue

        if not idxs:
            continue

        start_idx = min(idxs) - start_bar_idx
        end_idx = max(idxs) - start_bar_idx

        if start_idx < 0 or end_idx < 0:
            # Volta was entirely within dropped bars (or invalid).
            continue

        raw_voltas.append(
            (getattr(raw_volta, "number", None), start_idx, end_idx))

    return raw_voltas


def _renumber_bars_in_place(
        clean_part: music21.stream.Part,
) -> list[music21.stream.Measure]:
    """
    Renumber Measure objects in-place from 1..n and mark them explicit.

    Returns the list of bars after renumbering.

    Args:
        clean_part: The part to renumber

    Returns:
        List of renumbered measures
    """
    clean_bars = list(clean_part.getElementsByClass(music21.stream.Measure))
    for idx, clean_bar in enumerate(clean_bars, start=1):
        clean_bar.number = idx
        clean_bar.implicit = False
    return clean_bars


def _apply_voltas_by_bar_idx(
        *,
        clean_part: music21.stream.Part,
        clean_bars: list[music21.stream.Measure],
        raw_voltas: list[tuple[list[int] | str | None, int, int]],
) -> None:
    """
    Re-apply voltas (RepeatBracket) onto clean_part using bar indices.

    Args:
        clean_part: The part to populate
        clean_bars: The rebuilt measures
        raw_voltas: Volta snapshots to apply
    """
    for number, start_idx, end_idx in raw_voltas:
        if start_idx >= len(clean_bars) or end_idx >= len(clean_bars):
            continue

        clean_volta = spanner.RepeatBracket()
        if number is not None:
            clean_volta.number = number

        clean_volta.addSpannedElements(*clean_bars[start_idx:end_idx + 1])
        clean_part.insert(0.0, clean_volta)


# ============================================================================
# Main Normalization Functions
# ============================================================================

def normalize_part_for_lilypond(
        *,
        raw_part: music21.stream.Part,
        score_stream: music21.stream.Score,
        default_time_sig: meter.TimeSignature,
        pickup_cutoff_offset: float = 0.0,
) -> tuple[music21.stream.Part, bool]:
    """
    Normalize a Part/PartStaff so musicxml2ly is less likely to drop bars.

    Policy:
      - If pickup_cutoff_offset > 0.0, we drop pickup material in any part
        (content that occurs strictly before that cutoff) and shift the first
        post-pickup downbeat to offset 0.0 in the exported part.
      - If pickup_cutoff_offset == 0.0, we do not strip anything.

    Strategy:
      1) Compute which raw bars survive based on pickup_cutoff_offset.
      2) Snapshot barlines and voltas for the surviving bars.
      3) Rebuild a clean Part with stable offsets.
      4) Call makeMeasures() to enforce consistent bar structure.
      5) Re-apply barline formatting and voltas onto rebuilt bars.

    Returns:
        (clean_part, bar_count_changed)

    Args:
        raw_part: The original part to normalize
        score_stream: The parent score (for context lookups)
        default_time_sig: Fallback time signature
        pickup_cutoff_offset: Offset threshold for pickup stripping (in quarterLength)

    Returns:
        Tuple of (normalized_part, bar_count_changed_flag)
    """
    clean_part: music21.stream.Part = (
        music21.stream.PartStaff()
        if isinstance(raw_part, music21.stream.PartStaff)
        else music21.stream.Part()
    )

    time_sigs = list(
        raw_part.recurse().getElementsByClass(meter.TimeSignature))
    if not time_sigs:
        score_time_sigs = list(
            score_stream.recurse().getElementsByClass(meter.TimeSignature)
        )
        time_sigs = score_time_sigs[:] if score_time_sigs else [
            default_time_sig]

    raw_bars = list(
        raw_part.recurse().getElementsByClass(music21.stream.Measure))

    start_bar_idx = _start_bar_idx_after_cutoff(
        raw_bars=raw_bars,
        cutoff_offset=pickup_cutoff_offset,
    )

    barline_by_bar_idx = _snapshot_barlines_by_bar_idx(
        raw_bars=raw_bars,
        start_bar_idx=start_bar_idx,
    )
    raw_voltas = _snapshot_voltas_by_bar_idx(
        raw_part=raw_part,
        raw_bars=raw_bars,
        start_bar_idx=start_bar_idx,
    )

    _copy_timesigs_to_clean_part(
        clean_part=clean_part,
        time_sigs=time_sigs,
        cutoff_offset=pickup_cutoff_offset,
        default_time_sig=default_time_sig,
    )
    _copy_context_to_clean_part(
        raw_part=raw_part,
        clean_part=clean_part,
        cutoff_offset=pickup_cutoff_offset,
    )
    _copy_notes_and_rests_to_clean_part(
        raw_part=raw_part,
        clean_part=clean_part,
        cutoff_offset=pickup_cutoff_offset,
    )

    # Repair / normalize bar structure.
    # This is the step that previously reduced LilyPond "bar dropping" issues.
    clean_part.makeMeasures(inPlace=True)

    clean_bars = _renumber_bars_in_place(clean_part)
    _apply_barlines_by_bar_idx(
        clean_bars=clean_bars,
        barline_by_bar_idx=barline_by_bar_idx,
    )
    _apply_voltas_by_bar_idx(
        clean_part=clean_part,
        clean_bars=clean_bars,
        raw_voltas=raw_voltas,
    )

    bar_count_changed = False
    return clean_part, bar_count_changed


def build_export_score_for_lilypond(
        *,
        score_stream: music21.stream.Score,
        default_time_sig_str: str,
        score_label: str,
) -> music21.stream.Score:
    """
    Build a normalized export Score for musicxml2ly.

      - Always normalize parts.
      - If ANY part starts with a pickup/anacrusis, strip pickup material and
        shift the first post-pickup downbeat to offset 0.0 across ALL parts.
      - Attempt StaffGroup preservation; skip it if we can't remap safely.
      - Warn once per score if we encounter any structural concern during
        normalization/remapping.
      - Insert system breaks every 4 full measures to control PDF layout.

    Args:
        score_stream: The original score
        default_time_sig_str: Fallback time signature string (e.g., "4/4")
        score_label: Score identifier for warning messages

    Returns:
        Normalized score ready for LilyPond export
    """
    export_score = music21.stream.Score()
    default_time_sig = meter.TimeSignature(default_time_sig_str)

    raw_parts = list(score_stream.parts)
    staff_groups = list(
        score_stream.recurse().getElementsByClass(layout.StaffGroup)
    )

    structural_concern = False

    # Structural concern: no time signature anywhere in the input score.
    # In this case we will fall back to default_time_sig during normalization.
    if not list(
            score_stream.recurse().getElementsByClass(meter.TimeSignature)):
        structural_concern = True

    # ------------------------------------------------------------------
    # Pickup detection + score-level cutoff
    # ------------------------------------------------------------------
    # We consider the "post-pickup downbeat" to be the latest pickup end offset
    # among parts that start with a pickup.
    #
    # This is a simple, human-friendly rule that matches the established
    # policy:
    #   - Drop pickups wherever they exist.
    #   - Keep parts aligned by offset.
    #   - Shift the first real downbeat to 0.0 in the export.
    pickup_ends: list[float] = []
    for p in raw_parts:
        bars = list(p.recurse().getElementsByClass(music21.stream.Measure))
        end = _pickup_end_offset_if_pickup(
            raw_bars=bars,
            default_time_sig=default_time_sig,
        )
        if end is not None:
            pickup_ends.append(float(end))

    pickup_cutoff_offset = max(pickup_ends) if pickup_ends else 0.0

    part_map: dict[int, music21.stream.Part] = {}

    for raw_part in raw_parts:
        clean_part, bar_count_changed = normalize_part_for_lilypond(
            raw_part=raw_part,
            score_stream=score_stream,
            default_time_sig=default_time_sig,
            pickup_cutoff_offset=pickup_cutoff_offset,
        )
        if bar_count_changed:
            structural_concern = True

        part_map[id(raw_part)] = clean_part
        export_score.append(clean_part)

    # Try to preserve StaffGroup structures: if we can't safely remap,
    # skip and mark as a structural concern.
    for sg in staff_groups:
        sg_copy = copy.deepcopy(sg)

        getter = getattr(sg_copy, "getSpannedElements", None)
        setter = getattr(sg_copy, "addSpannedElements", None)
        if not (callable(getter) and callable(setter)):
            structural_concern = True
            continue

        new_spanned: list[music21.stream.Part] = []
        for el in list(getter()):
            mapped = part_map.get(id(el))
            if mapped is None:
                structural_concern = True
                new_spanned = []
                break
            new_spanned.append(mapped)

        if not new_spanned:
            continue

        if isinstance(getattr(sg_copy, "spannedElements", None), list):
            sg_copy.spannedElements = []
        setter(*new_spanned)

        export_score.insert(0.0, sg_copy)

    # ------------------------------------------------------------------
    # Insert system breaks every 4 full measures
    # ------------------------------------------------------------------
    insert_system_breaks_every_n_full_measures(
        export_score=export_score,
        default_time_sig=default_time_sig,
        measures_per_line=MEASURES_PER_LINE,
    )

    if structural_concern:
        warnings.warn(
            f"Score {score_label}: structural normalization issues detected. "
            f"Please inspect output; if issues are found, clean up the "
            f"input MusicXML and re-run.",
            UserWarning,
        )

    return export_score


def insert_system_breaks_every_n_full_measures(
        *,
        export_score: music21.stream.Score,
        default_time_sig: meter.TimeSignature,
        measures_per_line: int = 4,
) -> None:
    """
    Insert system breaks (line breaks) every N full measures to control PDF layout.

    Only counts "full" measures (bars whose duration matches the active time
    signature). Skips incomplete bars (pickups, turnarounds, etc.).

    Inserts breaks into the first part only (avoids redundancy in multi-staff
    scores). Breaks are inserted at the start of every Nth full measure.

    Special handling: Always adds a break after the first 4 full measures to ensure
    the first line contains exactly 4 measures, then continues the regular pattern.

    Args:
        export_score: The normalized score ready for export
        default_time_sig: Fallback time signature if none is found
        measures_per_line: Number of full measures before inserting a break (default: 4)
    """
    parts = list(export_score.parts)
    if not parts:
        return

    # Work with the first part only
    first_part = parts[0]
    measures = list(first_part.getElementsByClass(music21.stream.Measure))

    if len(measures) <= measures_per_line:
        # Score is too short to need breaks
        return

    full_measure_count = 0
    first_line_break_inserted = False

    for measure in measures:
        # Determine if this is a full measure
        time_sig = _active_time_signature(
            bar=measure,
            default_time_sig=default_time_sig,
        )

        expected_duration = float(time_sig.barDuration.quarterLength)
        actual_duration = float(measure.duration.quarterLength)

        # Check if this is a full measure (within tolerance)
        is_full = actual_duration >= (expected_duration - EPS_Q)

        if is_full:
            full_measure_count += 1

            # Special case: Always insert break after the first 4 full measures
            # to ensure the first line gets exactly 4 measures
            if not first_line_break_inserted and full_measure_count == measures_per_line:
                is_last_measure = (measure == measures[-1])
                if not is_last_measure:
                    first_line_break_inserted = True
                    # Find the next measure after this one
                    try:
                        current_idx = measures.index(measure)
                        if current_idx + 1 < len(measures):
                            next_measure = measures[current_idx + 1]
                            system_break = layout.SystemLayout(isNew=True)
                            next_measure.insert(0.0, system_break)
                    except (ValueError, IndexError):
                        pass
                continue

            # Regular pattern: insert break every N full measures
            # (starting from the second group of 4)
            if first_line_break_inserted and full_measure_count % measures_per_line == 0:
                is_last_measure = (measure == measures[-1])
                if not is_last_measure:
                    # Find the next measure
                    try:
                        current_idx = measures.index(measure)
                        if current_idx + 1 < len(measures):
                            next_measure = measures[current_idx + 1]
                            system_break = layout.SystemLayout(isNew=True)
                            next_measure.insert(0.0, system_break)
                    except (ValueError, IndexError):
                        pass