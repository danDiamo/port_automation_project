"""
pdf_utils.py holds helper functions for PDF file formatting, compilation, 
and watermarking.

Note: This module is not currently unit tested directly but is used by tests in
test_score.py.

"""

# TODO: Inspect new content

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
from music21 import bar, note, clef, key, meter, layout
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
        poet: str | None = None
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
    """
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
        # + poet (i.e. tune type) to pass through
        if title is None or not str(title).strip():
            raise ValueError(
                "PDF export requires score title to be "
                "provided for display in LilyPond header."
            )

        def _escape_ly(s: str) -> str:
            """Escape special characters for LilyPond header fields."""
            return str(s).strip().replace("\\", "\\\\").replace('"', '\\"')

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

    # Enforce page & line layout.
    # Always ensure \paper exists, then set bottom margin to reserve footer
    # space.
    footer_reserved_mm = 29
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

    # Overwrite existing bottom margin if present; otherwise insert it.
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
        measure: music21.stream.Measure,
        *,
        default_time_sig: meter.TimeSignature,
) -> meter.TimeSignature:
    """
    Resolve the active time signature for `measure`.

    Prefer an explicit time signature, otherwise use Music21 context lookup,
    and finally fall back to the project's default time signature.
    """
    return (
            measure.timeSignature
            or measure.getContextByClass(meter.TimeSignature)
            or default_time_sig
    )


def _is_incomplete_measure(
        measure: music21.stream.Measure,
        *,
        default_time_sig: meter.TimeSignature,
) -> bool:
    """
    True if the measure is shorter than the expected bar duration under the
    active time signature. This is our pickup/anacrusis heuristic.
    """
    ts = _active_time_signature(measure, default_time_sig=default_time_sig)
    expected = float(ts.barDuration.quarterLength)
    actual = float(measure.duration.quarterLength)
    return actual < (expected - _EPS_Q)


def _copy_all_at_offsets(
        *,
        src: music21.stream.Stream,
        dst: music21.stream.Stream,
        cls: type[music21.base.Music21Object],
) -> None:
    """
    Copy all elements of a given Music21 class from `src` into `dst`,
    preserving offsets.

    Used for clefs and key signatures to ensure the exported stream has enough
    context for conversion.
    """
    for el in src.recurse().getElementsByClass(cls):
        dst.insert(float(el.offset), copy.deepcopy(el))


def _normalize_part_for_lilypond(
        *,
        raw_part: music21.stream.Part,
        score_stream: music21.stream.Score,
        default_time_sig: meter.TimeSignature,
) -> tuple[music21.stream.Part, bool]:
    """
    Normalize a Part/PartStaff so musicxml2ly is less likely to drop bars.

    Returns:
        (clean_part, bar_count_changed)

    Key changes in this version:
      - We preserve bar boundaries instead of calling makeMeasures().
        This prevents pickups from being merged with first bar and avoids
        persistent issue with subsequent offsets shifting by half a bar.
    """
    clean_part: music21.stream.Part = (
        music21.stream.PartStaff()
        if isinstance(raw_part, music21.stream.PartStaff)
        else music21.stream.Part()
    )

    # Copy structural context: time signature(s), clef(s), key signature(s)
    time_sigs = list(
        raw_part.recurse().getElementsByClass(meter.TimeSignature))
    if not time_sigs:
        score_time_sigs = list(
            score_stream.recurse().getElementsByClass(meter.TimeSignature)
        )
        time_sigs = score_time_sigs[:] if score_time_sigs else [
            default_time_sig]

    for ts in time_sigs:
        clean_part.insert(float(ts.offset), copy.deepcopy(ts))

    _copy_all_at_offsets(src=raw_part, dst=clean_part, cls=clef.Clef)
    _copy_all_at_offsets(src=raw_part, dst=clean_part, cls=key.KeySignature)

    def _copy_notes_and_rests_with_grace_anchoring(
            src_stream: music21.stream.Stream,
            dst_stream: music21.stream.Stream,
    ) -> None:
        """
        Copy notes/rests from source src_stream to destination dst_stream. If
         grace notes are present in any voice their location/offset is
         preserved and they are placed immediately before the next
         non-grace object in the destination stream.

        This keeps ordering stable when grace notes share the same
        offset across voices.
        """
        pending_graces: list[music21.note.Note] = []

        for el in src_stream.notesAndRests:
            dur = getattr(el, "duration", None)
            is_grace = bool(dur is not None and getattr(dur, "isGrace", False))

            if is_grace and isinstance(el, note.Note):
                pending_graces.append(copy.deepcopy(el))
                continue

            anchor_offset = float(el.offset)

            if pending_graces:
                # inserts grace notes before anchor element
                for i, gn in enumerate(pending_graces):
                    back = _GRACE_ANCHOR_EPS * (len(pending_graces) - i)
                    dst_stream.insert(max(0.0, anchor_offset - back), gn)
                pending_graces.clear()

            dst_stream.insert(anchor_offset, copy.deepcopy(el))

    raw_measures = list(
        raw_part.recurse().getElementsByClass(music21.stream.Measure))

    for m_idx, raw_measure in enumerate(raw_measures):
        new_measure = music21.stream.Measure(
            number=getattr(raw_measure, "number", None))
        # set implicit True/False for pickup
        voices = list(raw_measure.getElementsByClass(music21.stream.Voice))
        if voices:
            for v in voices:
                new_voice = music21.stream.Voice()
                _copy_notes_and_rests_with_grace_anchoring(v, new_voice)
                new_measure.insert(float(v.offset), new_voice)
        else:
            _copy_notes_and_rests_with_grace_anchoring(raw_measure,
                                                       new_measure)

        # Insert the rebuilt bar at the original start offset.
        clean_part.insert(float(raw_measure.offset), new_measure)

    # Since we preserved bar boundaries, bar count does not change here.
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
      - Attempt StaffGroup preservation; skip it if we can't remap safely.
      - Warn once per score if we encounter any structural concern during
        normalization/remapping.
    """
    export_score = music21.stream.Score()
    default_time_sig = meter.TimeSignature(default_time_sig_str)

    raw_parts = list(score_stream.parts)
    staff_groups = list(
        score_stream.recurse().getElementsByClass(layout.StaffGroup))

    structural_concern = False

    # Structural concern: no time signature anywhere in the input score.
    # In this case we will fall back to DEFAULT_TIME_SIG during
    # normalization and warn the user via the warning at end of this function.
    if not list(
            score_stream.recurse().getElementsByClass(meter.TimeSignature)):
        structural_concern = True

    part_map: dict[int, music21.stream.Part] = {}

    for raw_part in raw_parts:
        clean_part, bar_count_changed = _normalize_part_for_lilypond(
            raw_part=raw_part,
            score_stream=score_stream,
            default_time_sig=default_time_sig,
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
