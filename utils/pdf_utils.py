"""
pdf_utils.py holds helper functions for PDF file formatting, compilation, 
and watermarking.

Note: This module is not currently unit tested directly but is used by tests in
test_score.py.

"""

# TODO: Inspect new content; line lengths/wrap

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


def _normalize_part_for_lilypond(
        *,
        raw_part: music21.stream.Part,
        score_stream: music21.stream.Score,
        default_time_sig: meter.TimeSignature,
        strict: bool,
        score_label: str,
) -> music21.stream.Part:
    """
    Normalize a Part/PartStaff so musicxml2ly doesn't drop bars.

    - Uses GLOBAL offsets when copying notes/rests (prevents accidental
    overlap/extra voices).
    - Copies time signatures, clefs, key signatures.
    - Rebars with makeMeasures().
    - If strict=True, requires that structural barlines can be remapped
    reliably
      (otherwise raises RuntimeError).
    """
    clean_part: music21.stream.Part
    if isinstance(raw_part, music21.stream.PartStaff):
        clean_part = music21.stream.PartStaff()
    else:
        clean_part = music21.stream.Part()

    # --- structural context: time sig / clef / key ---
    time_sigs_in_part = list(
        raw_part.recurse().getElementsByClass(meter.TimeSignature))
    if not time_sigs_in_part:
        time_sigs_from_score = list(
            score_stream.recurse().getElementsByClass(meter.TimeSignature))
        time_sigs_in_part = time_sigs_from_score[
            :] if time_sigs_from_score else [default_time_sig]

    for ts in time_sigs_in_part:
        clean_part.insert(float(ts.offset), copy.deepcopy(ts))

    for c in raw_part.recurse().getElementsByClass(clef.Clef):
        clean_part.insert(float(c.offset), copy.deepcopy(c))

    for ks in raw_part.recurse().getElementsByClass(key.KeySignature):
        clean_part.insert(float(ks.offset), copy.deepcopy(ks))

    # --- notes/rests with GLOBAL offsets + grace anchoring ---
    pending_graces: list[music21.note.Note] = []
    eps = 1e-4

    raw_bars = list(
        raw_part.recurse().getElementsByClass(music21.stream.Measure))
    for raw_bar in raw_bars:
        bar_offset = float(raw_bar.offset)

        for el in raw_bar.notesAndRests:
            dur = getattr(el, "duration", None)
            is_grace = bool(dur is not None and getattr(dur, "isGrace", False))

            if is_grace and isinstance(el, note.Note):
                pending_graces.append(copy.deepcopy(el))
                continue

            anchor_offset = bar_offset + float(el.offset)

            if pending_graces:
                for i, gn in enumerate(pending_graces):
                    back = eps * (len(pending_graces) - i)
                    clean_part.insert(max(0.0, anchor_offset - back), gn)
                pending_graces.clear()

            clean_part.insert(anchor_offset, copy.deepcopy(el))

    for gn in pending_graces:
        clean_part.append(gn)
    pending_graces.clear()

    # Rebar once content is correctly placed in time.
    clean_part.makeMeasures(inPlace=True)

    # Bars should be explicit to reduce downstream ambiguity
    for new_bar in clean_part.recurse().getElementsByClass(
            music21.stream.Measure):
        new_bar.implicit = False

    # --- strict remap of structural barlines (if required) ---
    if strict:
        new_bars = list(
            clean_part.recurse().getElementsByClass(music21.stream.Measure))
        if len(new_bars) != len(raw_bars):
            raise RuntimeError(
                f"Cannot preserve structure for {score_label}: "
                f"bar count changed during normalization (src="
                f"{len(raw_bars)}, dst={len(new_bars)})."
            )

        for old_bar, new_bar in zip(raw_bars, new_bars, strict=False):
            if getattr(old_bar, "leftBarline", None) is not None:
                new_bar.leftBarline = copy.deepcopy(old_bar.leftBarline)
            if getattr(old_bar, "rightBarline", None) is not None:
                new_bar.rightBarline = copy.deepcopy(old_bar.rightBarline)

            for bl in old_bar.getElementsByClass(bar.Barline):
                # If this fails, we do not want to silently degrade a
                # complex score.
                try:
                    new_bar.insert(float(bl.offset), copy.deepcopy(bl))
                except Exception as e:
                    raise RuntimeError(
                        f"Cannot preserve structure for {score_label}: "
                        f"failed to remap barline (type="
                        f"{getattr(bl, 'type', None)!r})."
                    ) from e

    return clean_part


def build_export_score_for_lilypond(
        *,
        score_stream: music21.stream.Score,
        default_time_sig_str: str,
        score_label: str,
) -> music21.stream.Score:
    """
    Build a normalized export Score suitable for musicxml2ly.

    Policy:
      - Simple tunes: normalize, but do not require strict structural
      remapping.
      - Complex inputs (PartStaff, staff groups, or structural barlines
      present):
        require strict remapping; any remap issue raises.
    """
    export_score = music21.stream.Score()
    default_time_sig = meter.TimeSignature(default_time_sig_str)

    raw_parts = list(score_stream.parts)
    staff_groups = list(
        score_stream.recurse().getElementsByClass(layout.StaffGroup))
    has_staff_groups = bool(staff_groups)

    part_map: dict[int, music21.stream.Part] = {}

    for raw_part in raw_parts:
        strict = (
                isinstance(raw_part, music21.stream.PartStaff)
                or _part_has_structural_barlines(raw_part)
                or has_staff_groups
        )

        clean_part = _normalize_part_for_lilypond(
            raw_part=raw_part,
            score_stream=score_stream,
            default_time_sig=default_time_sig,
            strict=strict,
            score_label=score_label,
        )
        part_map[id(raw_part)] = clean_part
        export_score.append(clean_part)

    # StaffGroup remap: if groups exist, treat as complex and fail if we
    # cannot remap.
    if has_staff_groups:
        for sg in staff_groups:
            sg_copy = copy.deepcopy(sg)

            getter = getattr(sg_copy, "getSpannedElements", None)
            setter = getattr(sg_copy, "addSpannedElements", None)
            if not (callable(getter) and callable(setter)):
                raise RuntimeError(
                    f"Cannot preserve staff grouping for {score_label}: "
                    "StaffGroup API unavailable for remapping."
                )

            old_spanned = list(getter())
            new_spanned: list[music21.stream.Part] = []
            for el in old_spanned:
                mapped = part_map.get(id(el))
                if mapped is None:
                    raise RuntimeError(
                        f"Cannot preserve staff grouping for {score_label}: "
                        "failed to remap a grouped part."
                    )
                new_spanned.append(mapped)

            clearer = getattr(sg_copy, "spannedElements", None)
            if isinstance(clearer, list):
                sg_copy.spannedElements = []
            setter(*new_spanned)

            export_score.insert(0.0, sg_copy)

    return export_score
