"""Launch options that change what the browser does, not who it claims to be:
each against its patch, with the default launch as the control.
"""

from __future__ import annotations

import base64
import datetime
import http.server
import io
import json
import ssl
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from _procs import options

HOST = "secure.test"
BODY = b"<!doctype html><title>trusted</title>"


def with_page(binary: Path, check: Any, **kwargs: Any) -> Any:
    """`check(page)` on a fresh page of a launch made with `kwargs`."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**options(binary, **kwargs))
        try:
            return check(browser.new_page())
        finally:
            browser.close()


# ---- 0033: closed shadow roots -------------------------------------------

SHADOW = """<div id="host"></div><script>
  document.getElementById('host').attachShadow({mode: 'closed'}).innerHTML = '<button>inside</button>';
</script>"""


def test_a_locator_reaches_into_a_closed_shadow_root_the_page_cannot(binary: Path) -> None:
    def check(page: Page) -> Tuple[int, Any]:
        page.set_content(SHADOW)
        return page.get_by_role("button", name="inside").count(), page.evaluate(
            "document.getElementById('host').shadowRoot")

    assert with_page(binary, check) == (1, None)


# ---- 0034: instant animations ----------------------------------------------

ANIMATE = """() => {
  const animation = document.body.animate({opacity: [0, 1]}, 60000);
  return Promise.race([animation.finished.then(() => 'finished'),
                       new Promise((done) => setTimeout(() => done(animation.playState), 2000))]);
}"""


@pytest.mark.parametrize("instant, state", [(True, "finished"), (None, "running")], ids=["on", "off"])
def test_instant_animations_finish_a_minute_long_animation_at_once(binary: Path, instant: Any, state: str) -> None:
    assert with_page(binary, lambda page: page.evaluate(ANIMATE), instant_animations=instant) == state


# ---- 0015: a configured connection goes through Chrome's rounding ----------

def test_a_configured_connection_is_rounded_as_a_measured_one(binary: Path) -> None:
    connection = with_page(binary, lambda page: page.evaluate(
        "({rtt: navigator.connection.rtt, downlink: navigator.connection.downlink})"),
        config={"connection.rtt": 120, "connection.downlink": 1.63})
    # Chrome scales each by a per-host factor in [0.9, 1.1], then rounds rtt to
    # 50 ms and downlink to 0.05 Mbps; the raw values are on neither grid.
    assert connection["rtt"] in (100, 150), connection
    assert round(connection["downlink"] * 20, 6).is_integer(), connection
    assert 1.45 <= connection["downlink"] <= 1.8, connection


# ---- 0035: extensions open no tabs unless allowed ------------------------

OPEN_TABS = """self.outcome = Promise.all([
  chrome.tabs.create({url: 'about:blank'}),
  chrome.windows.create({url: 'about:blank'}),
].map((opening) => opening.then(() => 'opened', (error) => error.message)));"""


@pytest.mark.parametrize("allow, outcome", [
    (True, ["opened", "opened"]),
    (None, ["Opening a tab is disabled", "Opening a tab is disabled"]),
], ids=["allowed", "default"])
def test_an_extension_opens_a_tab_only_when_allowed(binary: Path, tmp_path: Path, allow: Any,
                                                    outcome: List[str]) -> None:
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text(json.dumps({
        "manifest_version": 3, "name": "opens tabs", "version": "1",
        "background": {"service_worker": "background.js"}}))
    (extension / "background.js").write_text(OPEN_TABS)
    launch = options(binary, addons=[str(extension)], allow_addon_new_tab=allow)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(tmp_path / "profile"), **launch)
        try:
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker")
            assert worker.evaluate("self.outcome") == outcome
        finally:
            context.close()


# ---- 0036: CA certificates ---------------------------------------------------

def issue(tmp_path: Path) -> Tuple[Path, Path, Path]:
    """A CA, and a certificate it signs for HOST: (ca.pem, leaf.pem, leaf.key)."""
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    now = datetime.datetime.now(datetime.timezone.utc)

    def certificate(subject: str, key: Any, issuer: str, signer: Any, ca: bool) -> Any:
        builder = (x509.CertificateBuilder()
                   .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
                   .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=30))
                   .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
        if not ca:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        return builder.sign(signer, hashes.SHA256())

    ca_key, leaf_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = certificate("Test CA", ca_key, "Test CA", ca_key, ca=True)
    leaf = certificate(HOST, leaf_key, "Test CA", ca_key, ca=False)
    paths = tmp_path / "ca.pem", tmp_path / "leaf.pem", tmp_path / "leaf.key"
    paths[0].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths[1].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths[2].write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
    return paths


class Page200(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def https_server(tmp_path: Path) -> Iterator[Tuple[Path, int]]:
    ca, leaf, key = issue(tmp_path)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page200)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(leaf, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield ca, server.server_address[1]
    server.shutdown()
    thread.join()


@pytest.mark.parametrize("trusted", [True, False], ids=["trusted", "control"])
def test_a_certificate_signed_by_a_configured_ca_is_trusted(binary: Path, https_server: Tuple[Path, int],
                                                            trusted: bool) -> None:
    ca, port = https_server

    def check(page: Page) -> str:
        try:
            return page.goto(f"https://{HOST}:{port}/").body().decode()
        except PlaywrightError as exc:
            return exc.message

    got = with_page(binary, check, args=[f"--host-resolver-rules=MAP {HOST} 127.0.0.1"],
                    config={"certificatePaths": [str(ca)]} if trusted else {})
    if trusted:
        assert got == BODY.decode()
    else:
        assert "ERR_CERT_AUTHORITY_INVALID" in got, got


# ---- 0037: show cursor ---------------------------------------------------

def test_show_cursor_paints_a_ripple_where_automation_clicks(binary: Path) -> None:
    from PIL import Image, ImageChops  # noqa: PLC0415

    def frames(page: Page) -> Tuple[List[bytes], float]:
        """Screencast frames around a click at (200, 150), the first from before
        it, and the frames' pixels per CSS pixel. A frame is sent when the page
        repaints, so the ripple's frames arrive however slowly this side runs."""
        page.set_content("<body style='background:#fff'></body>")
        session = page.context.new_cdp_session(page)
        got: List[bytes] = []

        def on_frame(frame: Dict[str, Any]) -> None:
            got.append(base64.b64decode(frame["data"]))
            session.send("Page.screencastFrameAck", {"sessionId": frame["sessionId"]})

        session.on("Page.screencastFrame", on_frame)
        session.send("Page.startScreencast", {"format": "png"})
        while not got:
            page.wait_for_timeout(50)
        page.mouse.click(200, 150)
        page.wait_for_timeout(1000)
        return got, Image.open(io.BytesIO(got[0])).width / page.evaluate("innerWidth")

    def changed(binary: Path, **kwargs: Any) -> List[Tuple[float, ...]]:
        """Where each frame after the first differs from it, in CSS pixels."""
        data, scale = with_page(binary, frames, **kwargs)
        images = [Image.open(io.BytesIO(d)).convert("RGB") for d in data]
        boxes = [ImageChops.difference(images[0], image).getbbox() for image in images[1:]]
        return [tuple(edge / scale for edge in box) for box in boxes if box]

    on = changed(binary, show_cursor=True)
    assert on and all(box[0] <= 200 <= box[2] and box[1] <= 150 <= box[3] for box in on), on
    assert changed(binary) == []
