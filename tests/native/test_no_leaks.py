"""Launching and closing browsers leaves nothing behind.

A browser that outlives its `close()` holds memory and a profile lock until the
machine is rebooted, and on a server that runs thousands of sessions that is
the outage. Checked from outside the driver: the process table, not
Playwright's own bookkeeping.
"""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from _procs import browser_processes, options, wait_gone


def test_close_leaves_no_process_or_profile(binary: Path, rounds: int, browsers: int) -> None:
    assert not browser_processes()
    with sync_playwright() as playwright:
        for _ in range(rounds):
            launched = [playwright.chromium.launch(**options(binary)) for _ in range(browsers)]
            for browser in launched:
                page = browser.new_page()
                page.goto("data:text/html,<title>leak</title>")
                assert page.title() == "leak"
            tracked = browser_processes()
            assert len(tracked) >= browsers
            for browser in launched:
                browser.close()
            left = wait_gone(tracked)
            assert not left, f"{len(left)} browser process(es) outlived close(): {[p.pid for p in left]}"

def test_contexts_do_not_accumulate_processes(binary: Path) -> None:
    """Closing a context returns the browser to the process count it had."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**options(binary))
        try:
            def count() -> int:
                return len(browser_processes())

            def settle(target: int) -> int:
                deadline = time.monotonic() + 10
                while count() > target and time.monotonic() < deadline:
                    time.sleep(0.25)
                return count()

            warm = browser.new_context()
            warm.new_page().goto("data:text/html,warm")
            warm.close()
            baseline = settle(0)
            for _ in range(5):
                context = browser.new_context()
                context.new_page().goto("data:text/html,page")
                context.close()
            after = settle(baseline)
            assert after <= baseline, f"{baseline} processes before five contexts, {after} after closing them"
        finally:
            tracked = browser_processes()
            browser.close()
    assert not wait_gone(tracked)
