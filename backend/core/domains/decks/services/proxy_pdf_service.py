"""Lightweight PDF generator for printable proxy sheets.

The generator intentionally has **no runtime dependencies**. It emits a
small, standards-conformant PDF 1.4 document by hand. This keeps the
feature self-contained and sidesteps importing a heavy PDF library.

Layout
------
Letter-sized page (8.5in × 11in). A 3×3 grid of card slots at standard
Magic dimensions (2.5in × 3.5in) per page. Each card is rendered as a
bordered rectangle with the card name and optional set/collector-number
stamp inside. Card art is intentionally omitted — the output is designed
for home-printing playtest proxies where readability matters most.

This service is pure function over the decklist and can be called from a
route or a CLI.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Iterable, Sequence


__all__ = ["ProxySlot", "render_proxy_pdf"]


# Page + layout constants (all in PDF points, 72 per inch)
_PT = 1
_IN = 72 * _PT
_PAGE_WIDTH = int(8.5 * _IN)
_PAGE_HEIGHT = int(11 * _IN)
_CARD_WIDTH = int(2.5 * _IN)
_CARD_HEIGHT = int(3.5 * _IN)
_COLS = 3
_ROWS = 3
_CARDS_PER_PAGE = _COLS * _ROWS
_MARGIN_X = (_PAGE_WIDTH - _COLS * _CARD_WIDTH) // 2
_MARGIN_Y = (_PAGE_HEIGHT - _ROWS * _CARD_HEIGHT) // 2


@dataclass
class ProxySlot:
    name: str
    set_code: str | None = None
    collector_number: str | None = None
    mana_cost: str | None = None
    type_line: str | None = None


def _expand_deck(deck: Iterable[tuple[ProxySlot, int]]) -> list[ProxySlot]:
    expanded: list[ProxySlot] = []
    for slot, qty in deck:
        qty = max(0, int(qty or 0))
        expanded.extend([slot] * qty)
    return expanded


def _escape(text: str) -> str:
    """Escape a string for inclusion in a PDF text-showing operator."""
    sanitized = (text or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    # PDF strings are WinAnsi by default; strip characters outside printable ASCII
    # to avoid rendering garbage with the core Helvetica font.
    return re.sub(r"[^\x20-\x7e]", "?", sanitized)


def _build_page_stream(slots: Sequence[ProxySlot]) -> bytes:
    """Return a content-stream body rendering up to 9 cards on one page."""
    lines: list[str] = []
    # Thin border around each card.
    lines.append("0.5 w")
    for idx, slot in enumerate(slots):
        col = idx % _COLS
        row = idx // _COLS
        x = _MARGIN_X + col * _CARD_WIDTH
        y = _MARGIN_Y + (_ROWS - 1 - row) * _CARD_HEIGHT  # top row first
        lines.append(f"{x} {y} {_CARD_WIDTH} {_CARD_HEIGHT} re S")
        # Title at top of card.
        title_x = x + 6
        title_y = y + _CARD_HEIGHT - 18
        lines.append("BT")
        lines.append("/F1 11 Tf")
        lines.append(f"{title_x} {title_y} Td")
        lines.append(f"({_escape(slot.name)}) Tj")
        lines.append("ET")
        # Mana cost / type line below the title.
        detail_parts: list[str] = []
        if slot.mana_cost:
            detail_parts.append(slot.mana_cost.replace("{", "").replace("}", ""))
        if slot.type_line:
            detail_parts.append(slot.type_line)
        if detail_parts:
            lines.append("BT")
            lines.append("/F1 8 Tf")
            lines.append(f"{title_x} {title_y - 14} Td")
            lines.append(f"({_escape(' — '.join(detail_parts))}) Tj")
            lines.append("ET")
        # Set + collector number stamp near bottom-left.
        stamp_parts: list[str] = []
        if slot.set_code:
            stamp_parts.append(slot.set_code.upper())
        if slot.collector_number:
            stamp_parts.append(f"#{slot.collector_number}")
        if stamp_parts:
            lines.append("BT")
            lines.append("/F1 7 Tf")
            lines.append(f"{title_x} {y + 10} Td")
            lines.append(f"({_escape(' '.join(stamp_parts))}) Tj")
            lines.append("ET")
    body = "\n".join(lines) + "\n"
    return body.encode("latin-1", errors="replace")


def render_proxy_pdf(
    deck: Sequence[tuple[ProxySlot, int]],
    *,
    title: str | None = None,
) -> bytes:
    """Render a deck list into a printable PDF byte string.

    ``deck`` is a sequence of ``(ProxySlot, quantity)`` tuples. Each copy of
    a card becomes its own slot on the sheet so a full deck prints across
    multiple pages in the expected order.
    """
    expanded = _expand_deck(deck)
    pages: list[bytes] = []
    for i in range(0, max(1, len(expanded)), _CARDS_PER_PAGE):
        page_slots = expanded[i : i + _CARDS_PER_PAGE]
        pages.append(_build_page_stream(page_slots))

    # Build PDF objects
    objects: list[bytes] = []

    def add_object(body: bytes) -> int:
        objects.append(body)
        return len(objects)  # 1-based index

    # add_object returns 1-based indices; we capture them so the trailer
    # ``/Root 1 0 R`` matches the assembled object stream.
    add_object(b"<< /Type /Catalog /Pages 2 0 R >>")  # object 1: catalog
    pages_placeholder_id = add_object(b"")  # object 2: /Pages, filled in below
    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Build page objects.
    page_ids: list[int] = []
    for stream_data in pages:
        content_id = add_object(
            b"<< /Length %d >>\nstream\n" % len(stream_data)
            + stream_data
            + b"endstream"
        )
        page_body = (
            b"<< /Type /Page /Parent %d 0 R "
            b"/MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /F1 %d 0 R >> >> "
            b"/Contents %d 0 R >>"
            % (pages_placeholder_id, _PAGE_WIDTH, _PAGE_HEIGHT, font_id, content_id)
        )
        page_ids.append(add_object(page_body))

    # Fill in the Pages node once we know the child IDs.
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    pages_body = (
        b"<< /Type /Pages /Count %d /Kids [%s] >>" % (len(page_ids), kids)
    )
    objects[pages_placeholder_id - 1] = pages_body

    # Assemble full byte stream, tracking byte offsets for xref.
    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    buffer.write(b"%\xe2\xe3\xcf\xd3\n")

    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(b"%d 0 obj\n" % index)
        buffer.write(body)
        buffer.write(b"\nendobj\n")

    xref_offset = buffer.tell()
    buffer.write(b"xref\n")
    buffer.write(b"0 %d\n" % (len(objects) + 1))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets:
        buffer.write(b"%010d 00000 n \n" % offset)
    trailer = (
        b"trailer\n<< /Size %d /Root 1 0 R"
        % (len(objects) + 1)
    )
    if title:
        trailer += b" /Info << /Title (%s) >>" % _escape(title).encode("latin-1", errors="replace")
    trailer += b" >>\nstartxref\n"
    buffer.write(trailer)
    buffer.write(b"%d\n%%%%EOF\n" % xref_offset)
    return buffer.getvalue()
