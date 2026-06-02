"""
pdf_utils.py holds helper functions for PDF file formatting, compilation,
and watermarking.

Note: This module is not currently unit tested directly but is used by tests in
test_score.py.

"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import warnings
from pathlib import Path

import xml.etree.ElementTree as ET
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf._page import PageObject

# Import music21 normalization utilities
from .music21_normalization import build_export_score_for_lilypond

# ============================================================================
# Configuration Constants
# ============================================================================

# LilyPond \paper block settings (in millimeters)
FOOTER_RESERVED_MM = 36
LEFT_MARGIN_MM = 12
RIGHT_MARGIN_MM = 12

# LilyPond spacing settings (in staff spaces: 1 space ≈ 1.75mm ≈ 5 points)
HEADER_TOP_VSPACE = 4  # Space above title
HEADER_BOTTOM_VSPACE = 2  # Space between composer/tune type and music
TOP_SYSTEM_SPACING_BASIC_DISTANCE = 12  # Top padding for pages 2+
LAST_BOTTOM_SPACING_BASIC_DISTANCE = 14  # Bottom padding for last page


# ============================================================================
# External Tool Checks
# ============================================================================

def check_lilypond() -> bool:
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


# ============================================================================
# LilyPond Formatting - Refactored Architecture
# ============================================================================

def escape_lilypond_string(s: str) -> str:
    """
    Escape special characters for LilyPond string values.

    LilyPond strings must not contain literal newlines. We therefore
    normalize all whitespace to single spaces before escaping.

    Args:
        s: String to escape

    Returns:
        Escaped string safe for LilyPond
    """
    normalized = re.sub(r"\s+", " ", str(s)).strip()
    return normalized.replace("\\", "\\\\").replace('"', '\\"')


class LilyPondScore:
    """
    Manages a LilyPond source document and provides safe block manipulation.

    This class encapsulates LilyPond source text and provides methods for
    safely inserting content into various blocks (\header, \paper, \layout)
    while tracking markers to prevent duplicate insertions.
    """

    def __init__(self, text: str, suppress_header: bool = False):
        """
        Initialize with LilyPond source text.

        Args:
            text: The LilyPond source code
            suppress_header: Whether we're in SVG mode (suppress headers)
        """
        self._text = text
        self.suppress_header = suppress_header

    def ensure_block_exists(self, block_name: str) -> None:
        """
        Ensure a LilyPond block exists in the source.

        Args:
            block_name: Name of block (e.g., 'header', 'paper', 'layout')
        """
        pattern = rf"(?s)\\{block_name}\s*\{{"
        if not re.search(pattern, self._text):
            self._text += f"\n\\{block_name} {{\n}}\n"

    def insert_into_block(
        self, block_name: str, content: str, marker: str | None = None
    ) -> None:
        """
        Insert content into a LilyPond block.

        Args:
            block_name: Name of block (e.g., 'header', 'paper')
            content: Content to insert
            marker: Optional marker to prevent duplicate insertions
        """
        if marker and self.has_marker(marker):
            return

        self.ensure_block_exists(block_name)
        pattern = rf"(?s)(\\{block_name}\s*\{{)"
        # Escape backslashes in content for regex replacement
        content_escaped = content.replace("\\", "\\\\")
        self._text = re.sub(
            pattern, rf"\1\n{content_escaped}\n", self._text, count=1
        )

    def has_marker(self, marker: str) -> bool:
        """
        Check if a marker exists in the source.

        Args:
            marker: Marker string to check for

        Returns:
            True if marker exists
        """
        return marker in self._text

    def get_text(self) -> str:
        """Return the current LilyPond source text."""
        return self._text

    def set_text(self, text: str) -> None:
        """Update the LilyPond source text."""
        self._text = text

    def find_score_end_position(self) -> int:
        """
        Find the position after the \score block ends.

        Returns:
            Position in text, or len(text) if no \score block found
        """
        score_match = re.search(r"(?s)\\score\s*\{", self._text)
        if score_match is None:
            return len(self._text)

        start = score_match.end()
        depth = 1
        i = start

        while i < len(self._text) and depth > 0:
            ch = self._text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1

        return i if depth == 0 else len(self._text)


def clean_unwanted_content(text: str) -> str:
    """
    Remove tempo markings, instrument names, and unwanted header fields.

    This removes elements we never want in any output (PDF or SVG):
    - Tempo markings (\\tempo, tempoWholesPerMinute)
    - Instrument names (instrumentName, shortInstrumentName)
    - Unwanted header fields (subtitle, subsubtitle, piece)

    Args:
        text: LilyPond source text

    Returns:
        Cleaned text
    """
    # Remove explicit tempo markup and tempo settings
    text = re.sub(r"(?m)^\s*\\tempo\b.*$\n?", "", text)
    text = re.sub(
        r"(?m)^\s*\\set\s+Score\.tempoWholesPerMinute\s*=\s*.*$\n?", "", text
    )

    # Remove instrument name assignments
    text = re.sub(
        r"(?m)^\s*\\set\s+(Staff|Voice)\.(shortInstrumentName|instrumentName)\s*=\s*.*$\n?",
        "",
        text,
    )

    # Handle \new Staff \with { instrumentName = "..." }
    text = re.sub(
        r"(?s)(\\with\s*\{.*?)(\bshortInstrumentName\s*=\s*.*?)(.*?})",
        r"\1\3",
        text,
    )
    text = re.sub(
        r"(?s)(\\with\s*\{.*?)(\binstrumentName\s*=\s*.*?)(.*?})", r"\1\3", text
    )

    # Always suppress subtitle, subsubtitle, piece
    text = re.sub(
        r'(?m)^\s*(subtitle|subsubtitle|piece)\s*=\s*".*"\s*$\n?', "", text
    )

    return text.rstrip() + "\n\n"


def suppress_svg_header(score: LilyPondScore) -> None:
    """
    Suppress all header output for SVG mode.

    Sets all header fields to ##f so that SVG images show only musical content
    without any title/metadata.

    Args:
        score: The LilyPondScore to modify
    """
    header_content = r"""
title = ##f
subtitle = ##f
subsubtitle = ##f
piece = ##f
composer = ##f
poet = ##f
arranger = ##f
opus = ##f
tagline = ##f
""".strip()

    score.ensure_block_exists("header")
    text = score.get_text()
    text += f"\n\\header {{\n{header_content}\n}}\n"
    score.set_text(text)


def build_header(
    score: LilyPondScore,
    title: str,
    composer: str | None,
    poet: str | None,
    source: str | None,
) -> None:
    """
    Populate header block for PDF output.

    Ensures \header block exists and populates:
    - tagline = ##f (suppress LilyPond default)
    - title (required)
    - composer (or ##f if blank)
    - poet (tune type, or ##f if blank)
    - source (or ##f if blank)

    Args:
        score: The LilyPondScore to modify
        title: Score title (required)
        composer: Composer name (optional)
        poet: Tune type (optional)
        source: Collection/source name (optional)

    Raises:
        ValueError: If title is missing or blank
    """
    if not str(title).strip():
        raise ValueError(
            "PDF export requires score title to be provided for display in LilyPond header."
        )

    safe_title = escape_lilypond_string(title)
    score.ensure_block_exists("header")

    text = score.get_text()

    # Suppress tagline
    if re.search(r"(?m)^\s*tagline\s*=", text):
        text = re.sub(r"(?m)^\s*tagline\s*=.*$", "  tagline = ##f", text)
    else:
        text = re.sub(
            r"(?s)(\\header\s*\{)", r"\1\n  tagline = ##f", text, count=1
        )

    # Populate title
    if re.search(r"(?m)^\s*title\s*=", text):
        text = re.sub(r'(?m)^\s*title\s*=.*$', f'  title = "{safe_title}"', text)
    else:
        text = re.sub(
            r"(?s)(\\header\s*\{)", rf'\1\n  title = "{safe_title}"', text, count=1
        )

    # Populate composer
    composer_clean = str(composer).strip() if composer else ""
    composer_line = (
        f'  composer = "{escape_lilypond_string(composer_clean)}"'
        if composer_clean
        else "  composer = ##f"
    )
    if re.search(r"(?m)^\s*composer\s*=", text):
        text = re.sub(r"(?m)^\s*composer\s*=.*$", composer_line, text)
    else:
        text = re.sub(
            r"(?s)(\\header\s*\{)", rf"\1\n{composer_line}", text, count=1
        )

    # Populate poet (tune_type)
    poet_clean = str(poet).strip() if poet else ""
    poet_line = (
        f'  poet = "{escape_lilypond_string(poet_clean)}"'
        if poet_clean
        else "  poet = ##f"
    )
    if re.search(r"(?m)^\s*poet\s*=", text):
        text = re.sub(r"(?m)^\s*poet\s*=.*$", poet_line, text)
    else:
        text = re.sub(r"(?s)(\\header\s*\{)", rf"\1\n{poet_line}", text, count=1)

    # Populate source
    source_clean = str(source).strip() if source else ""
    source_line = (
        f'  source = "{escape_lilypond_string(source_clean)}"'
        if source_clean
        else "  source = ##f"
    )
    if re.search(r"(?m)^\s*source\s*=", text):
        text = re.sub(r"(?m)^\s*source\s*=.*$", source_line, text)
    else:
        text = re.sub(r"(?s)(\\header\s*\{)", rf"\1\n{source_line}", text, count=1)

    score.set_text(text)


def configure_paper(score: LilyPondScore) -> None:
    """
    Configure paper layout settings.

    Sets:
    - Margins (left, right, bottom for footer space)
    - ragged-last and ragged-last-bottom
    - Suppresses page numbers
    - For SVG mode: forces single-line layout

    Args:
        score: The LilyPondScore to modify
    """
    score.ensure_block_exists("paper")
    text = score.get_text()

    # For SVG mode: force single-line layout with very wide line-width
    # and disable automatic line breaking
    if score.suppress_header and "PORT_SVG_SINGLE_LINE" not in text:
        single_line_config = """  % PORT_SVG_SINGLE_LINE
      % Force all 4 bars onto a single line for SVG incipit images.
      % The wide line-width prevents automatic line breaking.
      % LilyPond's -dcrop flag will still trim the output to actual content width.
      line-width = 500\\mm
      % Disable automatic line breaking
      system-count = #1"""
        score.insert_into_block("paper", single_line_config, "PORT_SVG_SINGLE_LINE")

    # Ensure basic paper settings exist
    if not re.search(r"ragged-last\s*=", text):
        score.insert_into_block("paper", "  ragged-last = ##t")

    if not re.search(r"ragged-last-bottom\s*=", text):
        score.insert_into_block("paper", "  ragged-last-bottom = ##t")

    if not re.search(r"indent\s*=", text):
        score.insert_into_block("paper", "  indent = 0\\mm")

    if not re.search(r"short-indent\s*=", text):
        score.insert_into_block("paper", "  short-indent = 0\\mm")

    if not re.search(r"left-margin\s*=", text):
        score.insert_into_block("paper", f"  left-margin = {LEFT_MARGIN_MM}\\mm")

    if not re.search(r"right-margin\s*=", text):
        score.insert_into_block("paper", f"  right-margin = {RIGHT_MARGIN_MM}\\mm")

    # Suppress page numbers
    page_num_overrides = """  % PORT_SUPPRESS_PAGE_NUMBERS
      print-page-number = ##f"""
    score.insert_into_block("paper", page_num_overrides, "PORT_SUPPRESS_PAGE_NUMBERS")

    # Set bottom margin for footer (PDF mode only)
    if not score.suppress_header:
        text = score.get_text()
        if re.search(r"(?m)^\s*bottom-margin\s*=", text):
            text = re.sub(
                r"(?m)^\s*bottom-margin\s*=.*$",
                f"  bottom-margin = {FOOTER_RESERVED_MM}\\\\mm",
                text,
            )
        else:
            comment = "  % Reserve vertical space for the PDF footer/watermark."
            margin_line = f"  bottom-margin = {FOOTER_RESERVED_MM}\\mm"
            score.insert_into_block("paper", f"{comment}\n{margin_line}")
        score.set_text(text)

    # Ensure ragged-last-bottom is set
    text = score.get_text()
    if re.search(r"(?m)^\s*ragged-last-bottom\s*=", text):
        text = re.sub(
            r"(?m)^\s*ragged-last-bottom\s*=.*$", "  ragged-last-bottom = ##t", text
        )
        score.set_text(text)


def configure_spacing(score: LilyPondScore) -> None:
    """
    Configure vertical spacing for footer clearance and top padding.

    Sets:
    - last-bottom-spacing (clearance for footer area)
    - top-system-spacing (padding for pages 2+)

    Args:
        score: The LilyPondScore to modify
    """
    if score.suppress_header:
        return

    spacing_config = f"""  % PORT_LAST_BOTTOM_SPACING
  % Keep extra vertical space between the last system and the bottom of the page.
  % This encourages earlier page breaks when the last page is tight.
  last-bottom-spacing = #'((basic-distance . {LAST_BOTTOM_SPACING_BASIC_DISTANCE})
                          (minimum-distance . {LAST_BOTTOM_SPACING_BASIC_DISTANCE})
                          (padding . 2)
                          (stretchability . 0))
  % PORT_TOP_SYSTEM_SPACING
  % Keep extra vertical space between the music system and the top of the
  % page for second and subsequent pages, per ITMA's request.
  top-system-spacing = #'((basic-distance . {TOP_SYSTEM_SPACING_BASIC_DISTANCE})
                          (minimum-distance . {TOP_SYSTEM_SPACING_BASIC_DISTANCE})
                          (padding . 0)
                          (stretchability . 0))"""

    score.insert_into_block("paper", spacing_config, "PORT_LAST_BOTTOM_SPACING")


def apply_header_font(score: LilyPondScore) -> None:
    r"""
    Apply Arial font to PDF header text.

    Inserts bookTitleMarkup with Arial override and disables scoreTitleMarkup
    to avoid printing the header twice.

    Args:
        score: The LilyPondScore to modify
    """
    if score.suppress_header:
        return

    arial_overrides = f"""  % PORT_HEADER_FONT_ARIAL
  % Use Arial for the PDF header text (title/composer/etc.).
  % This affects the title markup blocks without changing global fonts
  % across the entire document.
  %
  % Increase whitespace above/below header blocks for readability.
  %
  % IMPORTANT:
  % We intentionally define bookTitleMarkup and disable scoreTitleMarkup to
  % avoid printing the header twice (book-level header + score-level header).
  bookTitleMarkup = \\markup \\override #'(font-name . "Arial") \\fill-line {{{{
    \\column {{{{
      \\vspace #{HEADER_TOP_VSPACE}
      \\fill-line {{{{ \\fontsize #6 \\fromproperty #'header:title }}}}
      \\vspace #1
      \\fill-line {{{{
        \\fontsize #1
        \\fromproperty #'header:composer
        \\fromproperty #'header:poet
      }}}}
      \\vspace #{HEADER_BOTTOM_VSPACE}
    }}}}
  }}}}
  scoreTitleMarkup = ##f"""

    score.insert_into_block("paper", arial_overrides, "PORT_HEADER_FONT_ARIAL")


def configure_layout(score: LilyPondScore) -> None:
    """
    Configure layout engravers and break alignment.

    Removes unwanted engravers (instrument names, metronome marks) and
    for PDFs, strengthens explicit system breaks.

    Args:
        score: The LilyPondScore to modify
    """
    text = score.get_text()

    # Add layout block to remove engravers
    layout_engravers = r"""
\layout {
  \context { \Staff \remove Instrument_name_engraver }
  \context { \Score \remove Metronome_mark_engraver }
}
""".lstrip()

    if "Instrument_name_engraver" not in text:
        text += f"\n{layout_engravers}\n"

    # For PDFs: strengthen explicit system breaks
    if not score.suppress_header and "PORT_PDF_BREAK_ALIGNMENT" not in text:
        break_alignment = r"""
% PORT_PDF_BREAK_ALIGNMENT
% Strengthen explicit system breaks and discourage LilyPond from
% overriding them based on note density.
\layout {
  \context {
    \Score
    % Increase the penalty for breaking anywhere except explicit breaks
    \override NonMusicalPaperColumn.line-break-penalty = #10000
    % Reduce spacing flexibility so bars don't compress/expand as much
    \override SpacingSpanner.base-shortest-duration = #(ly:make-moment 1/16)
    \override SpacingSpanner.spacing-increment = #1.2
    % Set uniform spacing between barlines
    \override SpacingSpanner.uniform-stretching = ##t
  }
}
""".lstrip()
        text += f"\n{break_alignment}\n"

    score.set_text(text)


def insert_collection_name(score: LilyPondScore, source: str | None) -> None:
    """
    Insert collection/source markup at the end of the document.

    Finds the end of the \score block and inserts a markup block displaying
    the collection name with Arial font.

    Args:
        score: The LilyPondScore to modify
        source: Collection/source name
    """
    if score.suppress_header:
        return

    if score.has_marker("PORT_SOURCE_AT_DOCUMENT_END"):
        return

    source_clean = str(source).strip() if source else ""
    if not source_clean:
        return

    insert_at = score.find_score_end_position()
    safe_source = escape_lilypond_string(source_clean)

    source_markup = f"""
% PORT_SOURCE_AT_DOCUMENT_END
% Print collection/source below the final system (end of document).
\\markup
  \\override #'(font-name . "Arial")
  \\fill-line {{{{
    \\left-column {{{{
      \\vspace #1
      \\fontsize #1
      \\wordwrap {{{{ "{safe_source}" }}}}
    }}}}
  }}}}
""".lstrip()

    text = score.get_text()
    prefix = text[:insert_at].rstrip() + "\n\n"
    suffix = "\n\n" + text[insert_at:].lstrip()
    score.set_text(prefix + source_markup.rstrip() + suffix)


def cleanup_lilypond_formatting(
    ly_text: str,
    *,
    suppress_header: bool = False,
    title: str | None = None,
    composer: str | None = None,
    poet: str | None = None,
    source: str | None = None,
) -> str:
    """
    Clean and format LilyPond source text for PDF or SVG export.

    Coordinator function that applies transformations in sequence:
    1. Clean unwanted content (tempo, instruments, headers)
    2. Configure header (populate OR suppress for SVG)
    3. Configure paper layout
    4. Configure spacing
    5. Apply header font
    6. Configure layout/engravers
    7. Insert collection name

    Args:
        ly_text: The LilyPond source text
        suppress_header: Whether to suppress the title/header markup (SVG mode)
        title: Score title (required for PDFs)
        composer: Composer name (optional)
        poet: Tune type / poet (optional)
        source: Collection/source name (optional)

    Returns:
        Cleaned LilyPond source text

    Raises:
        ValueError: If PDF mode and title is missing
    """
    # Step 1: Clean unwanted content
    ly_text = clean_unwanted_content(ly_text)

    # Step 2: Create document wrapper
    score = LilyPondScore(ly_text, suppress_header=suppress_header)

    # Step 3: Configure header
    if suppress_header:
        suppress_svg_header(score)
    else:
        build_header(score, title or "", composer, poet, source)

    # Step 4: Configure paper
    configure_paper(score)

    # Step 5: Configure spacing (PDF only)
    configure_spacing(score)

    # Step 6: Apply header font (PDF only)
    apply_header_font(score)

    # Step 7: Configure layout
    configure_layout(score)

    # Step 8: Insert collection name (PDF only)
    insert_collection_name(score, source)

    return score.get_text()


# ============================================================================
# SVG Utilities
# ============================================================================

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

    Args:
        svg_path: Path to the SVG file
        pad_top: Top padding (SVG user units)
        pad_right: Right padding (SVG user units)
        pad_bottom: Bottom padding (SVG user units)
        pad_left: Left padding (SVG user units)
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
    root.set("viewBox", f"{new_min_x:g} {new_min_y:g} {new_vb_w:g} {new_vb_h:g}")

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
        ET.tostring(root, encoding="unicode", method="xml"), encoding="utf-8"
    )


# ============================================================================
# PDF Utilities
# ============================================================================

def _add_pdf_footer(*, page: PageObject, footer: PageObject) -> None:
    """
    Overlay `footer` onto score in-place.

    Assumes footer artwork is already positioned at the bottom of its own page.
    We scale it to match the target page width and place at (0, 0).

    Args:
        page: The PDF page to add footer to
        footer: The footer page to overlay
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

    Args:
        pdf_path: Path to the PDF to add footer to
        footer_pdf_path: Path to the footer PDF

    Returns:
        Path to the modified PDF
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
        prefix=pdf_path.name + ".", suffix=".tmp", dir=str(pdf_path.parent)
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