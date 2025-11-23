"""UI regression tests for the Scryfall browser drawer."""
from __future__ import annotations

import contextlib
import json
import os
import platform
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Error as PlaywrightError, sync_playwright  # type: ignore  # noqa: E402


def _should_skip_playwright() -> bool:
    if (os.getenv("FORCE_PLAYWRIGHT") or "").lower() in {"1", "true", "yes", "on"}:
        return False
    if (os.getenv("SKIP_PLAYWRIGHT") or "").lower() in {"1", "true", "yes", "on"}:
        return True
    model_path = Path("/sys/firmware/devicetree/base/model")
    try:
        if model_path.exists() and "raspberry pi" in model_path.read_text(errors="ignore").lower():
            return True
    except Exception:
        pass
    machine = platform.machine().lower()
    if machine.startswith("arm") or "aarch64" in machine:
        return True
    return False


if _should_skip_playwright():
    pytest.skip(
        "Skipping Playwright UI tests on ARM/Pi or constrained hardware (set FORCE_PLAYWRIGHT=1 to run).",
        allow_module_level=True,
    )


@pytest.mark.ui
def test_scryfall_drawer_opens_when_row_clicked(live_server):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except PlaywrightError as exc:  # pragma: no cover - depends on local toolchain
            pytest.skip(f"Playwright browser binaries missing: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            drawer_payload = {
                "info": {
                    "name": "Lightning Bolt",
                    "type_line": "Instant",
                    "rarity_label": "Uncommon",
                    "set_name": "Magic 2011",
                    "set": "m11",
                    "collector_number": "146",
                    "mana_cost_html": "{R}",
                    "cmc": 1,
                    "oracle_text_html": "Lightning Bolt deals 3 damage to any target.",
                    "scryfall_uri": "https://example.com/cards/bolt",
                },
                "prices": {"usd": "3.00"},
                "image": {"large": "https://example.com/bolt-large.jpg"},
                "images": [],
            }

            page.route(
                "**/api/scryfall/print/**",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(drawer_payload),
                ),
            )

            page.goto(f"{live_server}/scryfall", wait_until="networkidle")
            page.wait_for_selector("tr.sfb-row")

            first_row = page.locator("tr.sfb-row").first
            first_row.click()

            drawer = page.locator("#sfbDrawer.show")
            drawer.wait_for(state="visible")

            assert "Lightning Bolt" in page.locator("#sfbDrawer").inner_text(timeout=2000)
            assert page.locator("#sfbDrawerBody .card-art").is_visible()
        finally:
            with contextlib.suppress(Exception):
                browser.close()
