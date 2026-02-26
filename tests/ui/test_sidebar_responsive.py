"""UI smoke tests for verifying the responsive sidebar behaviour.

These tests rely on Playwright. Install the Python package (already listed in
requirements) and run ``playwright install`` once to download the browser
engines before executing the suite.
"""

import contextlib
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
            header = page.locator(".app-header")
            backdrop = page.locator("#sidebarBackdrop")

            assert sidebar.is_visible(), "Sidebar should be visible on desktop width"
            assert (
                sidebar.get_attribute("aria-hidden") == "false"
            ), "Sidebar aria-hidden should be false on desktop"
            assert not toolbar.is_visible(), "Mobile toolbar should be hidden on desktop widths"
            assert header.is_visible(), "Desktop header should be visible on desktop widths"

            def assert_mobile_nav(viewport: dict[str, int]) -> None:
                page.set_viewport_size(viewport)
                page.wait_for_timeout(300)

                toolbar.wait_for(state="visible")
                assert toolbar.is_visible(), "Toolbar should appear on mobile/tablet widths"
                assert not header.is_visible(), "Desktop header should be hidden on mobile/tablet widths"
                assert (
                    sidebar.get_attribute("aria-hidden") == "true"
                ), "Sidebar should be hidden (aria-hidden true) before opening on mobile"
                assert page.locator(".content-toolbar .brand-link").is_visible()
                assert page.locator(".content-toolbar [data-theme-toggle]").is_visible()

                toggle_button = page.locator("#sidebarMobileToggle")
                toggle_button.click()
                page.wait_for_function("document.body.classList.contains('sidebar-open')")

                assert (
                    sidebar.get_attribute("aria-hidden") == "false"
                ), "Sidebar should expose aria-hidden=false after opening"
                assert backdrop.is_visible(), "Opening the sidebar should reveal the backdrop overlay"

                sidebar_box = sidebar.bounding_box()
                assert sidebar_box is not None
                assert sidebar_box["width"] <= viewport["width"] * 0.96

                first_link_box = page.locator("#sidebar .nav-link").first.bounding_box()
                assert first_link_box is not None
                assert first_link_box["height"] >= 40

                close_button = page.locator("#sidebarMobileClose")
                close_button.wait_for(state="visible")
                close_button.click()
                page.wait_for_function("!document.body.classList.contains('sidebar-open')")
                assert (
                    sidebar.get_attribute("aria-hidden") == "true"
                ), "Sidebar should hide again after tapping the close button"
                page.wait_for_timeout(150)
                assert not backdrop.is_visible()

            # iPad portrait-ish viewport
            assert_mobile_nav({"width": 820, "height": 1180})
            # typical phone viewport
            assert_mobile_nav({"width": 390, "height": 844})
        finally:
            with contextlib.suppress(Exception):
                browser.close()
