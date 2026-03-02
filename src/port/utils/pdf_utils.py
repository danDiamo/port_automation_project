"""
pdf_utils.py holds helper functions for PDF file formatting, compilation, 
and watermarking.

Note: This module is not currently unit tested directly but is used by tests in
test_score.py.

"""

# TODO: Inspect new content; rename & annotate

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import warnings
from pathlib import Path

import music21
import xml.etree.ElementTree as ET
from music21 import bar, note, clef, key, meter, layout, spanner
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf._page import PageObject


def check_lilypond():
    """
    Checks if LilyPond is installed. Returns True if found,
    False otherwise.
    """
    # Explicitly passing a string 'lilypond' for Windows compatibility
    if shutil.which(str('lilypond')) is None:
        warnings.warn(
            "LilyPond not found on system. PDF conversion is unavailable.",
            UserWarning
        )
        return False
    return True


def cleanup_lilypond_formatting(
        ly_text: str, *,
        suppress_header: bool = False,
        title: str | None = None,
        composer: str | None = None,
        poet: str | None = None,
        source: str | None = None
) -> str:
    """
    Remove instrument/voice labels (e.g. "Violin") and tempo/BPM markings
    from a temp LilyPond (.ly) file, which is created during the PDF
    export process.

    If suppress_header=True, remove header/title output (useful for
    incipit SVGs where we want to keep musical content only).

    For PDF output (set suppress_header=False), populate the following:
      - title (required; will fallback to 'untitled' if missing/blank)
      - composer (set to ##f if missing/blank)
      - poet (used for tune_type metadata; set to ##f if missing/blank)
      - source (optional; prints below the score on page 1 only)
    """

    def _set_pdf_header_font(text: str) -> str:

        """
        Forces Lilypond to use Arial for the PDF header text (
        title/composer/etc.) instead of default font.

        Also increases spacing between header elements and the score for
        readability.
        """

        if suppress_header:
            return text

        # Ensure there is a \paper block to attach overrides to.
        if not re.search(r"(?s)\\paper\s*\{", text):
            text += r"""
    \paper {
    }
    """.lstrip()

        # Pass if a previous run already inserted our overrides
        if "PORT_HEADER_FONT_ARIAL" in text:
            return text

        arial_overrides = r"""
  % PORT_HEADER_FONT_ARIAL
  % Use Arial for the PDF header text (title/composer/etc.).
  % This affects the title markup blocks without changing global fonts
  % across the entire document.
  %
  % Increase whitespace above/below header blocks for readability.
  bookTitleMarkup = \markup \override #'(font-name . "Arial") \fill-line {
    \column {
      \vspace #4
      \fill-line { \fontsize #6 \fromproperty #'header:title }
      \vspace #2
      \fill-line {
        \fontsize #1
        \fromproperty #'header:composer
        \fromproperty #'header:poet
      }
    }
  }
  scoreTitleMarkup = \markup \override #'(font-name . "Arial") \fill-line {
    \column {
      \vspace #4
      \fill-line { \fontsize #6 \fromproperty #'header:title }
      \vspace #2
      \fill-line {
        \fontsize #1
        \fromproperty #'header:composer
        \fromproperty #'header:poet
      }
    }
  }
""".rstrip()

        return re.sub(
            r"(?s)(\\paper\s*\{)",
            lambda m: f"{m.group(1)}\n{arial_overrides}\n",
            text,
            count=1,
        )

    def _escape_ly(s: str) -> str:
        """
        Escape special characters for LilyPond string values.
        """
        return str(s).strip().replace("\\", "\\\\").replace('"', '\\"')

    def _set_footer_textbox(text: str) -> str:
        """
        Add a first-page-only footer (left-aligned) below the score content.

        - Only show on page 1.
        - Text comes from metadata field 'source' if present.
        - Must be Arial.
        - Should sit above the PDF footer overlay without being obscured.

        """
        if suppress_header:
            return text

        # Ensure there is a \paper block to attach footer markup to.
        if not re.search(r"(?s)\\paper\s*\{", text):
            text += r"""
\paper {
}
""".lstrip()

        if "PORT_FIRST_PAGE_SOURCE_FOOTER" in text:
            return text

        footer_markup = r"""
  % PORT_FIRST_PAGE_SOURCE_FOOTER
  % Print the collection name below the score content on page 1 only.
  % Keep it above the PDF footer overlay by reserving enough bottom margin.
  oddFooterMarkup = \markup
    \override #'(font-name . "Arial")
    \on-the-fly #first-page
    \fill-line {
      \left-column {
        \fontsize #1
        \wordwrap-string \fromproperty #'header:source
      }
    }
  evenFooterMarkup = \oddFooterMarkup
""".rstrip()

        return re.sub(
            r"(?s)(\\paper\s*\{)",
            lambda m: f"{m.group(1)}\n{footer_markup}\n",
            text,
            count=1,
        )

    # Remove explicit tempo markup and tempo settings
    ly_text = re.sub(r"(?m)^\s*\\tempo\b.*$\n?", "", ly_text)
    ly_text = re.sub(
        r"(?m)^\s*\\set\s+Score\.tempoWholesPerMinute\s*=\s*.*$\n?",
        "",
        ly_text,
    )

    # Remove instrument name assignments
    ly_text = re.sub(
        r"(?m)^\s*\\set\s+(Staff|Voice)\.("
        r"shortInstrumentName|instrumentName)\s*=\s*.*$\n?", "",
        ly_text,
    )

    # Also handle: \new Staff \with { instrumentName = "Violin" ... }
    # Only strip these specific properties, leaving other \with settings
    # intact.
    ly_text = re.sub(
        r"(?s)(\\with\s*\{.*?)(\bshortInstrumentName\s*=\s*.*?)(.*?})",
        r"\1\3",
        ly_text,
    )
    ly_text = re.sub(
        r"(?s)(\\with\s*\{.*?)(\binstrumentName\s*=\s*.*?)(.*?})",
        r"\1\3",
        ly_text,
    )

    # Always suppress these header fields
    # (we do NOT want them in PDF or SVG).
    ly_text = re.sub(
        r'(?m)^\s*(subtitle|subsubtitle|piece)\s*=\s*".*"\s*$\n?',
        "",
        ly_text,
    )

    ly_text = ly_text.rstrip() + "\n\n"

    if suppress_header:
        # suppress all header output (title, composer, lyricist etc.)
        # for svg output
        ly_text += r"""
\header {
  title = ##f
  subtitle = ##f
  subsubtitle = ##f
  piece = ##f
  composer = ##f
  poet = ##f
  arranger = ##f
  opus = ##f
  tagline = ##f
}
""".lstrip()
    else:
        # PDFs: keep header but:
        # populate title from metadata, suppress subtitle, allow composer
        # & poet (i.e. tune type) to pass through
        if title is None or not str(title).strip():
            raise ValueError(
                "PDF export requires score title to be "
                "provided for display in LilyPond header."
            )

        safe_title = _escape_ly(title)

        # Ensure there is a header block
        if not re.search(r"(?s)\\header\s*\{", ly_text):
            ly_text += r"""
\header {
}
""".lstrip()

        # Suppress tagline/subtitle (either overwrite or insert)
        if re.search(r"(?m)^\s*tagline\s*=", ly_text):
            ly_text = re.sub(
                r"(?m)^\s*tagline\s*=.*$",
                "  tagline = ##f",
                ly_text,
            )
        else:
            ly_text = re.sub(
                r"(?s)(\\header\s*\{)",
                r"\1\n  tagline = ##f",
                ly_text,
                count=1,
            )

        # Populate title (overwrite or insert)
        if re.search(r"(?m)^\s*title\s*=", ly_text):
            ly_text = re.sub(
                r'(?m)^\s*title\s*=.*$',
                f'  title = "{safe_title}"',
                ly_text,
            )
        else:
            ly_text = re.sub(
                r"(?s)(\\header\s*\{)",
                rf'\1\n  title = "{safe_title}"',
                ly_text,
                count=1,
            )

        # Populate composer
        composer_clean = str(
            composer).strip() if composer is not None else ""
        composer_line = (
            f'  composer = "{_escape_ly(composer_clean)}"'
            if composer_clean
            else "  composer = ##f"
        )
        if re.search(r"(?m)^\s*composer\s*=", ly_text):
            ly_text = re.sub(
                r"(?m)^\s*composer\s*=.*$",
                composer_line,
                ly_text,
            )
        else:
            ly_text = re.sub(
                r"(?s)(\\header\s*\{)",
                rf"\1\n{composer_line}",
                ly_text,
                count=1,
            )

        # Populate tune_type (in 'poet' field)
        tune_type_clean = str(poet).strip() if poet is not None else ""
        poet_line = (
            f'  poet = "{_escape_ly(tune_type_clean)}"'
            if tune_type_clean
            else "  poet = ##f"
        )
        if re.search(r"(?m)^\s*poet\s*=", ly_text):
            ly_text = re.sub(
                r"(?m)^\s*poet\s*=.*$",
                poet_line,
                ly_text,
            )
        else:
            ly_text = re.sub(
                r"(?s)(\\header\s*\{)",
                rf"\1\n{poet_line}",
                ly_text,
                count=1,
            )

        # Populate source (for first-page-only footer line)
        source_clean = str(source).strip() if source is not None else ""
        source_line = (
            f'  source = "{_escape_ly(source_clean)}"'
            if source_clean
            else "  source = ##f"
        )
        if re.search(r"(?m)^\s*source\s*=", ly_text):
            ly_text = re.sub(
                r"(?m)^\s*source\s*=.*$",
                source_line,
                ly_text,
            )
        else:
            ly_text = re.sub(
                r"(?s)(\\header\s*\{)",
                rf"\1\n{source_line}",
                ly_text,
                count=1,
            )

    # Enforce page & line layout.
    # Always ensure \paper exists, then set bottom margin to reserve footer
    # space.
    footer_reserved_mm = 36
    if not re.search(r"(?s)\\paper\s*\{", ly_text):
        ly_text += r"""
\paper {
  ragged-last = ##t
  ragged-last-bottom = ##t
  indent = 0\mm
  short-indent = 0\mm
  left-margin = 12\mm
  right-margin = 12\mm
""".lstrip()

    # Apply Arial font to PDF header text.
    ly_text = _set_pdf_header_font(ly_text)

    # Add the first-page-only source footer line (Arial).
    ly_text = _set_footer_textbox(ly_text)

    # Overwrite existing bottom margin if present; insert if it's undefined.
    if re.search(r"(?m)^\s*bottom-margin\s*=", ly_text):
        ly_text = re.sub(
            r"(?m)^\s*bottom-margin\s*=.*$",
            f"  bottom-margin = {footer_reserved_mm}\\mm",
            ly_text,
        )
    else:
        ly_text = re.sub(
            r"(?s)(\\paper\s*\{)",
            rf"\1\n  % Reserve vertical space for the PDF footer/watermark.\n"
            rf"  bottom-margin = {footer_reserved_mm}\\mm",
            ly_text,
            count=1,
        )

    # ensure ragged-last-bottom so the last page
    # doesn't try to justify down into the margin area.
    if re.search(r"(?m)^\s*ragged-last-bottom\s*=", ly_text):
        ly_text = re.sub(
            r"(?m)^\s*ragged-last-bottom\s*=.*$",
            "  ragged-last-bottom = ##t",
            ly_text,
        )
    else:
        ly_text = re.sub(
            r"(?s)(\\paper\s*\{)",
            r"\1\n  ragged-last-bottom = ##t",
            ly_text,
            count=1,
        )

    # Disable engravers responsible for printing unwanted elements.
    ly_text += r"""
\layout {
  \context { \Staff \remove Instrument_name_engraver }
  \context { \Score \remove Metronome_mark_engraver }
}
""".lstrip()

    return ly_text


def pad_svg_file(
        svg_path: Path,
        *,
        pad_top: float = 12.0,
        pad_right: float = 12.0,
        pad_bottom: float = 12.0,
        pad_left: float = 12.0,
) -> None:
    """
    Add whitespace padding around an SVG by expanding its viewBox.
    Padding units are in SVG user units, not pixels/inches/cm.
    """
    svg_path = Path(svg_path)
    if not svg_path.exists():
        return

    data = svg_path.read_text(encoding="utf-8", errors="replace")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return

    view_box = root.get("viewBox")
    if not view_box:
        return

    parts = view_box.replace(",", " ").split()
    if len(parts) != 4:
        return

    try:
        min_x, min_y, vb_w, vb_h = (
            float(parts[0]),
            float(parts[1]),
            float(parts[2]),
            float(parts[3]),
        )
    except ValueError:
        return

    new_min_x = min_x - pad_left
    new_min_y = min_y - pad_top
    new_vb_w = vb_w + pad_left + pad_right
    new_vb_h = vb_h + pad_top + pad_bottom
    root.set("viewBox",
             f"{new_min_x:g} {new_min_y:g} {new_vb_w:g} {new_vb_h:g}")

    # If there's a clipPath with a rect sized to the old dimensions,
    # expand it so our padding doesn't obscure the drawing.
    for clip in root.iter():
        if not clip.tag.endswith("clipPath"):
            continue
        for el in list(clip):
            if not el.tag.endswith("rect"):
                continue
            el.set("x", f"{new_min_x:g}")
            el.set("y", f"{new_min_y:g}")
            el.set("width", f"{new_vb_w:g}")
            el.set("height", f"{new_vb_h:g}")

    svg_path.write_text(
        ET.tostring(root, encoding="unicode", method="xml"),
        encoding="utf-8",
    )


def _add_pdf_footer(*, page: PageObject, footer: PageObject) -> None:
    """
    Overlay `footer` onto score in-place.

    Assumes footer artwork is already positioned at the bottom of its own page.
    We scale it to match the target page width and place at (0, 0).
    """
    page_w = float(page.mediabox.width)
    footer_w = float(footer.mediabox.width)

    if footer_w <= 0:
        return

    scale = page_w / footer_w
    transform = Transformation().scale(sx=scale, sy=scale)
    # Prefer overlaying on top of page content when supported.
    try:
        page.merge_transformed_page(footer, transform, over=True)
    except TypeError:
        # Older pypdf versions don't support `over=`.
        page.merge_transformed_page(footer, transform)


def apply_pdf_footer_to_all_pages_in_score(
        pdf_path: str | os.PathLike[str],
        footer_pdf_path: str | os.PathLike[str],
) -> Path:
    """
    Overlay footer PDF onto every page of PDF doc at `pdf_path`.

      - Missing footer, unreadable PDFs, or write failures raise error.

    Writes atomically to temp file; replaces original PDF on success.
    """
    pdf_path = Path(pdf_path)
    footer_pdf_path = Path(footer_pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if not footer_pdf_path.exists():
        raise FileNotFoundError(f"Footer PDF not found: {footer_pdf_path}")

    reader = PdfReader(str(pdf_path))
    footer_reader = PdfReader(str(footer_pdf_path))

    if not footer_reader.pages:
        raise ValueError(f"Footer PDF has no pages: {footer_pdf_path}")

    footer_page = footer_reader.pages[0]

    writer = PdfWriter()
    for p in reader.pages:
        page = p
        _add_pdf_footer(page=page, footer=footer_page)
        writer.add_page(page)

    fd, tmp_name = tempfile.mkstemp(
        prefix=pdf_path.name + ".",
        suffix=".tmp",
        dir=str(pdf_path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        with tmp_path.open("wb") as fp:
            writer.write(fp)
        os.replace(tmp_path, pdf_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return pdf_path


def _part_has_structural_barlines(raw_part: music21.stream.Part) -> bool:
    """
    True if the source part contains barlines that define internal structure
    (repeat/double/final).

    We treat these as "structural" because they may reflect repeat/section
    boundaries that rebarring could inadvertently change.
    """
    for raw_bar in raw_part.recurse().getElementsByClass(
            music21.stream.Measure):
        lb = getattr(raw_bar, "leftBarline", None)
        rb = getattr(raw_bar, "rightBarline", None)

        for b in (lb, rb):
            if b is not None and getattr(b, "type", None) in ("double",
                                                              "final",
                                                              "repeat"):
                return True

        for bl in raw_bar.getElementsByClass(bar.Barline):
            if getattr(bl, "type", None) in ("double", "final", "repeat"):
                return True

    return False


_EPS_Q = 1e-6  # quarterLength tolerance for "incomplete bar" comparisons
_GRACE_ANCHOR_EPS = 1e-4  # small negative offsets to keep grace notes ordered


def _active_time_signature(
        bar: music21.stream.Measure,
        *,
        default_time_sig: meter.TimeSignature,
) -> meter.TimeSignature:
    """
    Resolve the active time signature for `bar`.

    Prefer an explicit time signature, otherwise use Music21 context lookup,
    and finally fall back to the project's default time signature.
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
    """
    ts = _active_time_signature(bar, default_time_sig=default_time_sig)
    expected = float(ts.barDuration.quarterLength)
    actual = float(bar.duration.quarterLength)
    return actual < (expected - _EPS_Q)


def _barline_type_string(raw_barline: bar.Barline | None) -> str | None:
    """
    Return a barline type string if present, otherwise None.

    We preserve whatever barline types we receive in the input, because
    LilyPond
    output should retain encoded barline formatting when possible.
    """
    if raw_barline is None:
        return None
    t = getattr(raw_barline, "type", None)
    if t is None:
        return None
    s = str(t).strip()
    return s or None


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
    """
    if cutoff_offset <= 0.0:
        return 0

    for idx, b in enumerate(raw_bars):
        bar_end = float(b.offset + b.duration.quarterLength)
        if bar_end > (cutoff_offset + _EPS_Q):
            return idx

    return len(raw_bars)


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
    """
    if not time_sigs:
        clean_part.insert(0.0, copy.deepcopy(default_time_sig))
        return

    time_sigs_sorted = sorted(time_sigs, key=lambda t: float(t.offset))

    if cutoff_offset > 0.0:
        active_ts: meter.TimeSignature | None = None
        for ts in time_sigs_sorted:
            if float(ts.offset) <= cutoff_offset + _EPS_Q:
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
        if cutoff_offset > 0.0 and ts_off < cutoff_offset - _EPS_Q:
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
    """
    for cls in (clef.Clef, key.KeySignature):
        els = list(raw_part.recurse().getElementsByClass(cls))
        if not els:
            continue

        els_sorted = sorted(els, key=lambda e: float(e.offset))

        if cutoff_offset > 0.0:
            active_el = None
            for el in els_sorted:
                if float(el.offset) <= cutoff_offset + _EPS_Q:
                    active_el = el
                else:
                    break
            if active_el is not None:
                clean_part.insert(0.0, copy.deepcopy(active_el))

        for el in els_sorted:
            el_off = float(el.offset)
            if cutoff_offset > 0.0 and el_off < cutoff_offset - _EPS_Q:
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
    grace-note
    ordering.

    Any content before cutoff_offset is dropped. Remaining content is
    shifted so
    cutoff_offset becomes 0.0.
    """
    pending_graces: list[music21.note.Note] = []

    for el in raw_part.flatten().notesAndRests:
        dur = getattr(el, "duration", None)
        is_grace = bool(dur is not None and getattr(dur, "isGrace", False))

        if is_grace and isinstance(el, note.Note):
            pending_graces.append(copy.deepcopy(el))
            continue

        anchor_offset = float(el.offset)

        if pending_graces:
            # Insert grace notes immediately before the next non-grace event.
            for i, gn in enumerate(pending_graces):
                back = _GRACE_ANCHOR_EPS * (len(pending_graces) - i)
                ins = max(0.0, anchor_offset - back)
                if ins >= cutoff_offset - _EPS_Q:
                    clean_part.insert(max(0.0, ins - cutoff_offset), gn)
            pending_graces.clear()

        if anchor_offset < cutoff_offset - _EPS_Q:
            continue

        clean_part.insert(
            max(0.0, anchor_offset - cutoff_offset),
            copy.deepcopy(el),
        )


def _snapshot_barlines_by_bar_idx(
        *,
        raw_bars: list[music21.stream.Measure],
        start_bar_idx: int,
) -> list[tuple[str | None, str | None]]:
    """
    Snapshot left/right barline types by bar index so we can re-apply them
    after
    makeMeasures().

    The indices are relative to the kept bar sequence (i.e., raw_bars sliced
    from start_bar_idx onward).
    """
    barline_by_bar_idx: list[tuple[str | None, str | None]] = []
    for raw_bar in raw_bars[start_bar_idx:]:
        barline_by_bar_idx.append(
            (
                _barline_type_string(getattr(raw_bar, "leftBarline", None)),
                _barline_type_string(getattr(raw_bar, "rightBarline", None)),
            )
        )
    return barline_by_bar_idx


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
    """
    clean_bars = list(clean_part.getElementsByClass(music21.stream.Measure))
    for idx, clean_bar in enumerate(clean_bars, start=1):
        clean_bar.number = idx
        clean_bar.implicit = False
    return clean_bars


def _apply_barlines_by_bar_idx(
        *,
        clean_bars: list[music21.stream.Measure],
        barline_by_bar_idx: list[tuple[str | None, str | None]],
) -> None:
    """
    Re-apply left/right barline types onto clean bars by index.
    """
    for clean_bar, (lb_t, rb_t) in zip(clean_bars, barline_by_bar_idx,
                                       strict=False):
        if lb_t:
            clean_bar.leftBarline = bar.Barline(lb_t)
        if rb_t:
            clean_bar.rightBarline = bar.Barline(rb_t)


def _apply_voltas_by_bar_idx(
        *,
        clean_part: music21.stream.Part,
        clean_bars: list[music21.stream.Measure],
        raw_voltas: list[tuple[list[int] | str | None, int, int]],
) -> None:
    """
    Re-apply voltas (RepeatBracket) onto clean_part using bar indices.
    """
    for number, start_idx, end_idx in raw_voltas:
        if start_idx >= len(clean_bars) or end_idx >= len(clean_bars):
            continue

        clean_volta = spanner.RepeatBracket()
        if number is not None:
            clean_volta.number = number

        clean_volta.addSpannedElements(*clean_bars[start_idx:end_idx + 1])
        clean_part.insert(0.0, clean_volta)


def _normalize_part_for_lilypond(
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
        clean_part, bar_count_changed = _normalize_part_for_lilypond(
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

    if structural_concern:
        warnings.warn(
            f"Score {score_label}: structural normalization issues detected. "
            f"Please inspect output; if issues are found, clean up the "
            f"input MusicXML and re-run.",
            UserWarning,
        )

    return export_score
