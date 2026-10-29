"""Reading a PDF's text layer locally, and finding the text a reader can't see.

The text path never sends a PDF to a model: its text is read here, masked, and only the masked
text goes out. Text that is drawn in white or at a size no one can read is kept apart: it is
how instructions are planted in documents, so it is screened, flagged and never extracted from.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from pypdf import Configuration, PdfReader, apply_configuration

TINY_POINTS = 4.0  # text drawn smaller than this can't be read on the page

# pypdf's own limits allow 75 MB per stream, and a 600 KB upload built to expand that far took
# the reader past 400 MB, more than the API may use. A real payslip or statement page holds a
# few kilobytes of drawing instructions, so these limits leave it a wide margin.
_MB = 2_000_000
LIMITED = Configuration().with_overwrites(
    zlib_maximum_output_length=_MB,
    lzw_maximum_output_length=_MB,
    run_length_maximum_output_length=_MB,
    brotli_maximum_output_length=_MB,
    jbig2_maximum_output_length=_MB,
    array_based_stream_maximum_output_length=_MB,
    image_maximum_buffer_size=_MB,
    stream_decoding_work_maximum_length=2 * _MB,
    stream_parsing_work_maximum_count=100_000,
    xform_maximum_invocations_per_extraction=50,
)


def opened(data: bytes) -> PdfReader:
    """A reader for an uploaded PDF. Use it inside `with apply_configuration(LIMITED)`."""
    return PdfReader(io.BytesIO(data))


@dataclass
class PdfText:
    pages: int
    visible: str
    hidden: list[str] = field(default_factory=list)


def _is_white(colour: tuple[float, ...]) -> bool:
    if len(colour) == 1:  # gray
        return colour[0] >= 0.95
    if len(colour) == 3:  # RGB
        return all(c >= 0.95 for c in colour)
    if len(colour) == 4:  # CMYK
        return all(c <= 0.05 for c in colour)
    return False


def _read_page(page: Any, visible: list[str], hidden: list[str]) -> None:
    fill: tuple[float, ...] = (0.0,)
    saved: list[tuple[float, ...]] = []  # q pushes the graphics state, Q pops it

    def before(op: bytes, args: list[Any], cm: Any, tm: Any) -> None:
        nonlocal fill
        if op in (b"g", b"rg", b"k"):
            fill = tuple(float(a) for a in args)
        elif op == b"q":
            saved.append(fill)
        elif op == b"Q" and saved:
            fill = saved.pop()

    def text(chunk: str, cm: Any, tm: Any, font: Any, size: float) -> None:
        if not chunk:
            return
        seen = float(size or 0) * (abs(float(tm[3]) * float(cm[3])) or 1.0)
        unseen = _is_white(fill) or 0 < seen < TINY_POINTS
        (hidden if chunk.strip() and unseen else visible).append(chunk)

    page.extract_text(visitor_operand_before=before, visitor_text=text)
    visible.append("\n")


def _squash(text: str) -> str:
    return " ".join(text.split()).lower()


def read_pdf(data: bytes) -> PdfText:
    """The text a reader sees, laid out in its columns, and apart from it, the hidden text.

    Two passes. The first follows the drawing operators to tell visible text from hidden. The
    second lays the page out with its columns kept (a statement's Debit and Credit columns are
    otherwise indistinguishable once flattened), and drops every line that is wholly hidden.
    """
    with apply_configuration(LIMITED):
        reader = opened(data)
        flat: list[str] = []
        hidden: list[str] = []
        for page in reader.pages:
            _read_page(page, flat, hidden)
        hidden_text = " ".join(" ".join(hidden).split())
        secret = _squash(hidden_text)
        lines = []
        for page in reader.pages:
            for line in page.extract_text(extraction_mode="layout").splitlines():
                if secret and _squash(line) and _squash(line) in secret:
                    continue
                lines.append(line.rstrip())
            lines.append("")
        return PdfText(
            pages=len(reader.pages),
            visible="\n".join(lines).strip(),
            hidden=[hidden_text] if hidden_text else [],
        )
