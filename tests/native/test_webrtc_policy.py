"""WebRTC stays off the host's own network when the launcher says it should.

With a proxy, the launcher passes `--force-webrtc-ip-handling-policy=
disable_non_proxied_udp` (also with `block_webrtc=True`). Chrome's full browser
used to ignore that switch, since only content_shell read it, so a proxied
launch still sent STUN from this host and showed its public address as the
srflx candidate (0030). A page sees the policy as the candidates it gets, so
that is what is checked. No ICE server is needed: an unblocked launch gathers an
mDNS host candidate with none, and a blocked one gathers nothing.

The proxy is never contacted. The policy only needs one to be configured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from playwright.sync_api import sync_playwright

from _procs import options

GATHER = """async () => {
  const pc = new RTCPeerConnection();
  const candidates = [];
  pc.onicecandidate = (e) => e.candidate && candidates.push(e.candidate.candidate);
  pc.createDataChannel('x');
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((done) => {
    if (pc.iceGatheringState === 'complete') done();
    pc.onicegatheringstatechange = () => pc.iceGatheringState === 'complete' && done();
    setTimeout(done, 10000);
  });
  const sdp = pc.localDescription.sdp.split('\\r\\n').filter((l) => /^(c=|a=candidate)/.test(l));
  pc.close();
  return { candidates, sdp };
}"""

UNREACHABLE_PROXY = {"server": "socks5://127.0.0.1:1"}


def gather(binary: Path, **kwargs: Any) -> Dict[str, List[str]]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**options(binary, **kwargs))
        try:
            page = browser.new_page()
            page.goto("about:blank")
            return page.evaluate(GATHER)
        finally:
            browser.close()


def test_an_unblocked_launch_gathers_a_host_candidate(binary: Path) -> None:
    """The control: without it, an empty list below could mean WebRTC never ran."""
    got = gather(binary)
    assert any(" typ host " in c for c in got["candidates"]), got


@pytest.mark.parametrize(
    "proxy, block_webrtc",
    [(UNREACHABLE_PROXY, None), (None, True)],
    ids=["proxy", "block_webrtc"],
)
def test_a_blocked_launch_gathers_nothing(
    binary: Path, proxy: Optional[Dict[str, str]], block_webrtc: Optional[bool]
) -> None:
    got = gather(binary, proxy=proxy, block_webrtc=block_webrtc)
    assert got["candidates"] == [], got
    assert got["sdp"] == ["c=IN IP4 0.0.0.0"], got
