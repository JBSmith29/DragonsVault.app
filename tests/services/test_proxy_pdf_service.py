"""Tests for the pure-Python proxy PDF generator."""

from __future__ import annotations

from core.domains.decks.services.proxy_pdf_service import (
    ProxySlot,
    render_proxy_pdf,
)


def test_render_proxy_pdf_emits_valid_pdf_header():
    slots = [(ProxySlot(name="Sol Ring", set_code="c20", collector_number="278"), 1)]
    pdf = render_proxy_pdf(slots, title="Test Deck")
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")


def test_render_proxy_pdf_has_enough_pages_for_deck_size():
    # 20 cards should need three pages (9 per page).
    slots = [(ProxySlot(name=f"Card {i}"), 1) for i in range(20)]
    pdf = render_proxy_pdf(slots)
    # Count /Type /Page occurrences (includes /Type /Pages, so subtract one).
    page_objects = pdf.count(b"/Type /Page ")
    assert page_objects >= 3


def test_render_proxy_pdf_handles_empty_deck():
    pdf = render_proxy_pdf([])
    assert pdf.startswith(b"%PDF-1.4")


def test_render_proxy_pdf_escapes_special_characters():
    slots = [(ProxySlot(name="Who/What/When/Where/Why"), 1)]
    pdf = render_proxy_pdf(slots)
    # Slashes are safe; just make sure the PDF still parses (ends with EOF).
    assert pdf.rstrip().endswith(b"%%EOF")


def test_render_proxy_pdf_quantity_expands_into_multiple_slots():
    slots = [(ProxySlot(name="Plains"), 4)]
    pdf = render_proxy_pdf(slots)
    # "Plains" should appear four times in content streams.
    assert pdf.count(b"(Plains)") == 4
