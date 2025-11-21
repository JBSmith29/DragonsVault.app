"""UI smoke tests for verifying the responsive sidebar behaviour.

These tests rely on Playwright. Install the Python package (already listed in
requirements) and run ``playwright install`` once to download the browser
engines before executing the suite.
"""

import contextlib

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Error as PlaywrightError, sync_playwright  # type: ignore  # noqa: E402


@pytest.mark.ui
def test_sidebar_behaves_on_desktop_and_mobile(live_server):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except PlaywrightError as exc:  # pragma: no cover - depends on local tooling
            pytest.skip(f"Playwright browser binaries missing: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})

            page.goto(f"{live_server}/", wait_until="networkidle")
            page.wait_for_load_state("networkidle")
            page.wait_for_selector("#sidebar")

            sidebar = page.locator("#sidebar")
            toolbar = page.locator(".content-toolbar")

            assert sidebar.is_visible(), "Sidebar should be visible on desktop width"
            assert (
                sidebar.get_attribute("aria-hidden") == "false"
            ), "Sidebar aria-hidden should be false on desktop"
            assert not toolbar.is_visible(), "Mobile toolbar should be hidden on desktop widths"

            # Switch to a mobile viewport
            page.set_viewport_size({"width": 540, "height": 900})
            page.wait_for_timeout(250)

            toolbar.wait_for(state="visible")
            assert toolbar.is_visible(), "Toolbar should appear on mobile widths"
            assert (
                sidebar.get_attribute("aria-hidden") == "true"
            ), "Sidebar should be hidden (aria-hidden true) before opening on mobile"

            toggle_button = page.locator("#sidebarMobileToggle")
            toggle_button.click()
            page.wait_for_function("document.body.classList.contains('sidebar-open')")

            assert (
                sidebar.get_attribute("aria-hidden") == "false"
            ), "Sidebar should expose aria-hidden=false after opening"
            assert (
                page.locator("#sidebarBackdrop").is_visible()
            ), "Opening the sidebar should reveal the backdrop overlay"

            close_button = page.locator("#sidebarMobileClose")
            close_button.wait_for(state="visible")
            close_button.click()
            page.wait_for_function("!document.body.classList.contains('sidebar-open')")

            assert (
                sidebar.get_attribute("aria-hidden") == "true"
            ), "Sidebar should hide again after tapping the close button"
            page.wait_for_timeout(150)
            assert not page.locator("#sidebarBackdrop").is_visible()
        finally:
            with contextlib.suppress(Exception):
                browser.close()
