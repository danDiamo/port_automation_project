"""
This module contains unit tests for the pdf_utils module. Note: Most
pdf_utils functionality is tested indirectly by tests in test_score.py.
"""

import music21
from music21 import bar, meter, note, spanner, stream

from port.utils.pdf_utils import build_export_score_for_lilypond


def _make_bar(number: int,
              quarter_lengths: list[float]) -> music21.stream.Measure:
    """
    Helper to create a single test bar.
    """
    m = music21.stream.Measure(number=number)
    for ql in quarter_lengths:
        n = note.Note("C4")
        n.duration.quarterLength = ql
        m.append(n)
    return m


def test_build_export_score_strips_pickup_and_aligns_parts_by_offset():
    """
    If any part starts with a pickup/anacrusis, we drop the pickup material.

    Policy (established in this conversation):
      - Pickups are stripped (removed) wherever they exist.
      - Parts remain structurally aligned by musical offset.
      - The first "real downbeat" after pickup stripping becomes offset 0.0
        in the exported score for *all* parts.

    This test uses a realistic (non-overlapping) setup:
      - Part 1 has a 1-beat pickup in 4/4, then a full bar.
      - Part 2 has only a full bar, starting at the same downbeat offset
        as Part 1's first full bar (i.e., no overlapping full-bar content).
    """
    score = stream.Score()

    # Part 1: pickup (1 quarter), then a full bar starting at offset 1.0.
    p1 = stream.Part()
    p1.insert(0.0, meter.TimeSignature("4/4"))
    p1.insert(0.0, _make_bar(1, [1.0]))
    p1.insert(1.0, _make_bar(2, [1.0, 1.0, 1.0, 1.0]))

    # Part 2: a full bar only, starting at the same downbeat offset (1.0).
    p2 = stream.Part()
    p2.insert(0.0, meter.TimeSignature("4/4"))
    p2.insert(1.0, _make_bar(1, [1.0, 1.0, 1.0, 1.0]))

    score.insert(0.0, p1)
    score.insert(0.0, p2)

    export = build_export_score_for_lilypond(
        score_stream=score,
        default_time_sig_str="4/4",
        score_label="unit-test-score",
    )

    export_parts = list(export.parts)
    assert len(export_parts) == 2

    export_p1_bars = list(export_parts[0].getElementsByClass(stream.Measure))
    export_p2_bars = list(export_parts[1].getElementsByClass(stream.Measure))

    # After stripping the pickup and shifting, both parts should start at 0.0
    # and contain exactly one full bar.
    assert len(export_p1_bars) == 1
    assert len(export_p2_bars) == 1
    assert float(export_p1_bars[0].offset) == 0.0
    assert float(export_p2_bars[0].offset) == 0.0


def test_build_export_score_preserves_structural_barlines_and_voltas():
    """
    Ensure barline formatting and voltas survive export normalization.

    We attach barlines + a RepeatBracket (volta) to the input, run export
    normalization, and verify they're still present.
    """
    # ... existing code ...
    score = stream.Score()
    part = stream.Part()
    part.insert(0.0, meter.TimeSignature("4/4"))

    bar_1 = _make_bar(1, [1.0, 1.0, 1.0, 1.0])
    bar_1.rightBarline = bar.Barline("double")

    bar_2 = _make_bar(2, [1.0, 1.0, 1.0, 1.0])
    bar_2.rightBarline = bar.Barline("final")

    # Volta (RepeatBracket) spanning bar 1 only.
    rb = spanner.RepeatBracket()
    rb.number = [1]
    rb.addSpannedElements(bar_1)

    part.append(bar_1)
    part.append(bar_2)
    part.insert(0.0, rb)
    score.insert(0.0, part)

    export = build_export_score_for_lilypond(
        score_stream=score,
        default_time_sig_str="4/4",
        score_label="unit-test-barlines-voltas",
    )

    export_part = list(export.parts)[0]
    export_bars = list(export_part.getElementsByClass(stream.Measure))
    assert len(export_bars) == 2

    assert export_bars[0].rightBarline is not None
    assert export_bars[0].rightBarline.type == "double"
    assert export_bars[1].rightBarline is not None
    assert export_bars[1].rightBarline.type == "final"

    export_voltas = list(
        export_part.recurse().getElementsByClass(spanner.RepeatBracket)
    )
    assert len(export_voltas) == 1
    export_volta = export_voltas[0]

    # Verify number and span.
    assert getattr(export_volta, "number", None) in ([1], "1")
    spanned = list(export_volta.getSpannedElements())
    assert len(spanned) == 1
