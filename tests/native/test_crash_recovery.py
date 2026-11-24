"""A browser that dies is reported as dead, and the next launch is unaffected."""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from _procs import browser_processes, main_processes, options, wait_gone


def test_killed_browser_fails_its_pages_and_next_launch_works(binary: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**options(binary))
        page = browser.new_page()
        page.goto("data:text/html,alive")
        tracked = browser_processes()
        roots = main_processes()
        assert len(roots) == 1, f"expected one main browser process, found {len(roots)}"
        roots[0].kill()

        # Asked of the page rather than of `browser.is_connected()`: what a
        # script notices is that its next call fails, and it must fail rather
        # than hang.
        failure = None
        deadline = time.monotonic() + 15
        while failure is None and time.monotonic() < deadline:
            try:
                page.evaluate("1")
                time.sleep(0.2)
            except Exception as exc:  # noqa: BLE001 -- any error is the report
                failure = exc
        assert failure is not None, "a page of a killed browser still answers"
        left = wait_gone(tracked)
        assert not left, f"children of a killed browser are still running: {[p.pid for p in left]}"

        again = playwright.chromium.launch(**options(binary))
        try:
            page = again.new_page()
            page.goto("data:text/html,<title>again</title>")
            assert page.title() == "again"
        finally:
            tracked = browser_processes()
            again.close()
    assert not wait_gone(tracked)


def test_renderer_crash_is_contained(binary: Path) -> None:
    """One crashed tab does not take the browser, or its other tabs, with it."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**options(binary))
        try:
            survivor = browser.new_page()
            survivor.goto("data:text/html,<title>survivor</title>")
            victim = browser.new_page()
            victim.goto("data:text/html,victim")
            crashed = []
            victim.on("crash", lambda _: crashed.append(True))
            try:
                victim.goto("chrome://crash", timeout=10_000)
            except Exception:  # noqa: BLE001 -- navigation to a crash never completes
                pass
            deadline = time.monotonic() + 10
            while not crashed and time.monotonic() < deadline:
                survivor.wait_for_timeout(200)
            assert crashed, "the page reported no crash"
            assert browser.is_connected()
            assert survivor.title() == "survivor"
            assert survivor.evaluate("1 + 1") == 2
        finally:
            browser.close()
