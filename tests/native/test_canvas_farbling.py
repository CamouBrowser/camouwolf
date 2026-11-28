"""Canvas readback is farbled as Brave farbles it, and only with a token (0047).

An http(s) page reads the drawing with the low bit of a few hundred channel
values flipped, the same on every read and through every readback API, and
differently on another site or in another launch. A page that is not http(s)
reads the pixels as drawn, which is the reference here.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Dict, List

from PIL import Image
from playwright.sync_api import sync_playwright

from _procs import options

SITES = ("https://a.test/", "https://b.test/")

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
  const read = () => Array.from(context.getImageData(0, 0, 300, 60).data);
  const first = read();
  const urls = [canvas.toDataURL(), canvas.toDataURL()];
  return {reads: [first, read()], urls};
}"""


def decoded(url: str) -> List[int]:
    image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGBA")
    return list(image.tobytes())


def readback(binary: Path, canvas_noise: bool) -> Dict[str, dict]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**options(binary, canvas_noise=canvas_noise))
        try:
            page = browser.new_page()
            page.route("https://*.test/", lambda route: route.fulfill(body="<title>canvas</title>",
                                                                       content_type="text/html"))
            seen = {}
            for url in ("about:blank", *SITES):
                page.goto(url)
                seen[url] = page.evaluate(READS)
            return seen
        finally:
            browser.close()


def flipped(drawn: List[int], read: List[int]) -> List[int]:
    changed = [i for i, (a, b) in enumerate(zip(drawn, read)) if a != b]
    assert all(abs(drawn[i] - read[i]) == 1 for i in changed), "farbling changed more than a value's low bit"
    return changed


def test_an_http_page_reads_a_farbled_canvas_the_same_every_time(binary: Path) -> None:
    first, second = readback(binary, True), readback(binary, True)
    for seen in (first, second):
        drawn = seen["about:blank"]["reads"][0]
        assert decoded(seen["about:blank"]["urls"][0]) == drawn, "a page that is not http(s) was farbled"
        for site in SITES:
            reads, urls = seen[site]["reads"], seen[site]["urls"]
            assert reads[0] == reads[1], "toDataURL changed what getImageData reads next"
            assert urls[0] == urls[1]
            assert 0 < len(flipped(drawn, reads[0])) <= 512
            assert 0 < len(flipped(drawn, decoded(urls[0]))) <= 512
        assert flipped(drawn, seen[SITES[0]]["reads"][0]) != flipped(drawn, seen[SITES[1]]["reads"][0])
    assert flipped(first["about:blank"]["reads"][0], first[SITES[0]]["reads"][0]) != flipped(
        second["about:blank"]["reads"][0], second[SITES[0]]["reads"][0]), "two launches farbled one site alike"


def test_a_launch_without_a_token_reads_the_canvas_as_drawn(binary: Path) -> None:
    seen = readback(binary, False)
    drawn = seen["about:blank"]["reads"][0]
    for site in SITES:
        assert seen[site]["reads"] == [drawn, drawn]
        assert decoded(seen[site]["urls"][0]) == drawn
