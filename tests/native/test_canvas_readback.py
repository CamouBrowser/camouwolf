"""A 2D canvas reads back the same pixels from its first getImageData on.

An accelerated canvas answers its first read from the GPU and, after a few
reads, from a CPU copy. Ganesh unpremultiplied the GPU read in a shader, which
rounds low-alpha pixels differently from the CPU, so read 1 differed from read
2 on Linux and on no real Windows machine (0037). SwiftShader with the
blocklist ignored gives an accelerated canvas without a GPU, so the runner can
reproduce it.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from _procs import options

ACCELERATED = ["--ignore-gpu-blocklist", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]

READS = """() => {
  const canvas = document.createElement('canvas');
  canvas.width = 300; canvas.height = 60;
  const context = canvas.getContext('2d');
  context.textBaseline = 'top';
  context.font = '14px Arial';
  context.fillStyle = '#f60';
  context.fillRect(100, 1, 62, 20);
  context.fillStyle = '#069';
  context.fillText('Cwm fjordbank glyphs vext quiz', 2, 15);
  context.fillStyle = 'rgba(102, 204, 0, 0.2)';
  context.fillText('Cwm fjordbank glyphs vext quiz', 4, 30);
  return [0, 1, 2].map(() => Array.from(context.getImageData(0, 0, 300, 60).data));
}"""


def test_the_first_read_of_an_accelerated_canvas_is_the_rest(binary: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**options(binary, args=ACCELERATED))
        try:
            status = browser.new_browser_cdp_session().send("SystemInfo.getInfo")["gpu"]["featureStatus"]
            # Without acceleration every read comes from the CPU and the test proves nothing.
            assert status.get("2d_canvas", "").startswith("enabled"), status
            page = browser.new_page()
            page.goto("about:blank")
            first, second, third = page.evaluate(READS)
        finally:
            browser.close()
    differ = sum(a != b for a, b in zip(first, second))
    assert differ == 0, f"{differ} channel values of read 1 differ from read 2"
    assert second == third
