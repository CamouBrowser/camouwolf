"""A proxied launch's WebRTC looks like Chrome on a machine at the proxy's exit (0031).

Behind a SOCKS5 proxy, STUN goes through the proxy's UDP ASSOCIATE relay
(RFC 1928), so the srflx candidate is the address the STUN server saw: the
exit's. A STUN server named by host name is named to the relay, which resolves
it. The host candidate, the SDP and getStats() hold the identity's address
(`webrtc:ipv4`), never one of this machine's. Behind an HTTP proxy, which cannot
carry UDP, nothing is sent. An unproxied launch is unchanged.

The test runs its own SOCKS5 proxy and the internet behind it. The relay appears
there at the exit address, and the STUN server at a TEST-NET address that
nothing on this machine answers, so every request it sees came through the
relay. Where a leak would go straight from this machine, a STUN server listens
on this machine's own address instead, and must hear nothing.

Capture permission (a fake microphone) is what opens a real host candidate in
stock Chrome, so the no-host-address checks capture.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import struct
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

import psutil
import pytest
from playwright.sync_api import sync_playwright

from _procs import options

EXIT = "203.0.113.9"
STUN_ADDR = ("198.51.100.1", 3478)
STUN_NAME = "stun.relay.test"
MAPPED_IP = "198.51.100.20"
COOKIE = 0x2112A442
MEDIA = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]

GATHER = """async ([urls, capture]) => {
  const stream = capture ? await navigator.mediaDevices.getUserMedia({audio: true}) : null;
  const pc = new RTCPeerConnection({iceServers: [{urls}]});
  if (stream) stream.getTracks().forEach((t) => pc.addTrack(t, stream));
  else pc.createDataChannel('x');
  const candidates = [];
  const ended = new Promise((done) => {
    setTimeout(done, 30000);
    pc.onicecandidate = (e) => e.candidate ? candidates.push(e.candidate.candidate) : done();
  });
  await pc.setLocalDescription(await pc.createOffer());
  await ended;
  const stats = [];
  (await pc.getStats()).forEach((s) => s.type === 'local-candidate' && stats.push(s));
  const sdp = pc.localDescription.sdp;
  pc.close();
  if (stream) stream.getTracks().forEach((t) => t.stop());
  return {candidates, sdp, stats};
}"""


def binding_response(request: bytes, ip: str, port: int) -> Optional[bytes]:
    """The answer to an RFC 8489 Binding request, mapping it to ip:port; None if it is not one."""
    if len(request) < 20:
        return None
    kind, _, cookie = struct.unpack("!HHI", request[:8])
    if kind != 0x0001 or cookie != COOKIE:
        return None
    attr = struct.pack("!HHBBHI", 0x0020, 8, 0, 1, port ^ (COOKIE >> 16),
                       int(ipaddress.IPv4Address(ip)) ^ COOKIE)
    return struct.pack("!HHI", 0x0101, len(attr), COOKIE) + request[8:20] + attr


def own_address() -> str:
    """This machine's address on its default route. A UDP connect sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(STUN_ADDR)
        return probe.getsockname()[0]


def host_addresses() -> Set[str]:
    """Every address of this machine's interfaces but loopback."""
    found = set()
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family in (socket.AF_INET, socket.AF_INET6):
                ip = addr.address.split("%")[0]
                if not ipaddress.ip_address(ip).is_loopback:
                    found.add(ip)
    return found


class Internet:
    """The network behind the proxy: datagrams between registered addresses."""

    def __init__(self) -> None:
        self.hosts: Dict[Tuple[str, int], Any] = {}

    def send(self, payload: bytes, src: Tuple[str, int], dst: Tuple[str, int]) -> None:
        if dst in self.hosts:
            self.hosts[dst](payload, src)


class Stun:
    """Answers Binding requests with the source it saw. `mapped` overrides the
    address, for one on this machine that sees its clients across a NAT."""

    def __init__(self, mapped: Optional[str] = None) -> None:
        self.mapped = mapped
        self.requests: List[Tuple[str, int]] = []
        self.answered: Set[str] = set()

    def answer(self, data: bytes, src: Tuple[str, int]) -> Optional[bytes]:
        ip = self.mapped or src[0]
        response = binding_response(data, ip, src[1])
        if response:
            self.requests.append(src)
            self.answered.add(f"{ip}:{src[1]}")
        return response


class LocalStun(Stun, asyncio.DatagramProtocol):
    """A STUN server on this machine's own address, where a leak would arrive."""

    def connection_made(self, transport: Any) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        response = self.answer(data, addr)
        if response:
            self.transport.sendto(response, addr)


class Relay(asyncio.DatagramProtocol):
    """One association: its socket here, and the exit address on the internet."""

    def __init__(self, proxy: "Socks5Proxy", public: Tuple[str, int]) -> None:
        self.proxy, self.public = proxy, public
        self.client: Optional[Tuple[str, int]] = None
        proxy.net.hosts[public] = self.to_client

    def connection_made(self, transport: Any) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        self.client = addr
        if len(data) < 10 or data[:3] != b"\0\0\0" or data[3] not in (1, 3):
            return
        if data[3] == 1:
            host, rest = socket.inet_ntoa(data[4:8]), data[8:]
        else:
            host, rest = data[5:5 + data[4]].decode(), data[5 + data[4]:]
        port, payload = struct.unpack("!H", rest[:2])[0], rest[2:]
        self.proxy.destinations.append((data[3], host, port))
        self.proxy.net.send(payload, self.public, ({STUN_NAME: STUN_ADDR[0]}.get(host, host), port))

    def to_client(self, payload: bytes, src: Tuple[str, int]) -> None:
        if self.client:
            header = b"\0\0\0\x01" + socket.inet_aton(src[0]) + struct.pack("!H", src[1])
            self.transport.sendto(header + payload, self.client)


class Socks5Proxy:
    """A SOCKS5 proxy that only does UDP ASSOCIATE, relaying from the exit address."""

    def __init__(self, net: Internet) -> None:
        self.net = net
        self.destinations: List[Tuple[int, str, int]] = []  # (atyp, host, port) relayed
        self.relays: List[Relay] = []

    async def start(self) -> str:
        self.server = await asyncio.start_server(self.serve, "127.0.0.1", 0)
        return f"socks5://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            _, count = await reader.readexactly(2)
            await reader.readexactly(count)
            writer.write(b"\x05\x00")
            _, cmd, _, atyp = await reader.readexactly(4)
            await reader.readexactly({1: 4, 4: 16}.get(atyp) or (await reader.readexactly(1))[0])
            await reader.readexactly(2)
            if cmd != 3:
                writer.write(b"\x05\x07\x00\x01" + bytes(6))
                return
            public = (EXIT, 40000 + len(self.relays))
            _, relay = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: Relay(self, public), local_addr=("127.0.0.1", 0))
            self.relays.append(relay)
            port = relay.transport.get_extra_info("sockname")[1]
            writer.write(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", port))
            await writer.drain()
            # The association lives as long as its control connection.
            await reader.read()
            relay.transport.close()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@pytest.fixture(scope="module")
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join()


def run(loop: asyncio.AbstractEventLoop, coro: Any) -> Any:
    return asyncio.run_coroutine_threadsafe(coro, loop).result(10)


@pytest.fixture
def internet(loop: asyncio.AbstractEventLoop) -> Tuple[Internet, Stun, str, Socks5Proxy]:
    net = Internet()
    stun = Stun()

    def on_stun(data: bytes, src: Tuple[str, int]) -> None:
        response = stun.answer(data, src)
        if response:
            net.send(response, STUN_ADDR, src)

    net.hosts[STUN_ADDR] = on_stun
    proxy = Socks5Proxy(net)
    return net, stun, run(loop, proxy.start()), proxy


@pytest.fixture
def local_stun(loop: asyncio.AbstractEventLoop) -> Iterator[Tuple[LocalStun, str]]:
    async def start() -> Tuple[Any, LocalStun]:
        return await loop.create_datagram_endpoint(
            lambda: LocalStun(MAPPED_IP), local_addr=(own_address(), 0))

    transport, stun = run(loop, start())
    yield stun, "stun:{}:{}".format(*transport.get_extra_info("sockname"))
    loop.call_soon_threadsafe(transport.close)


def gather(binary: Path, urls: Any, capture: bool, **kwargs: Any) -> Dict[str, Any]:
    launch = options(binary, args=MEDIA, config={"webrtc:ipv4": EXIT}, **kwargs)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch)
        try:
            page = browser.new_context(permissions=["microphone"] if capture else []).new_page()
            page.goto("about:blank")
            return page.evaluate(GATHER, [urls, capture])
        finally:
            browser.close()


def srflx(got: Dict[str, Any]) -> Dict[str, Set[str]]:
    """The srflx address:port a page reads, from each of the three places."""
    pattern = re.compile(r" (\S+) (\d+) typ srflx")
    return {
        "candidates": {":".join(m) for c in got["candidates"] for m in pattern.findall(c)},
        "sdp": {":".join(m) for m in pattern.findall(got["sdp"])},
        "stats": {f"{s['address']}:{s['port']}" for s in got["stats"] if s.get("candidateType") == "srflx"},
    }


@pytest.mark.parametrize("server", [f"stun:{STUN_ADDR[0]}:{STUN_ADDR[1]}", f"stun:{STUN_NAME}:{STUN_ADDR[1]}"],
                         ids=["address", "name"])
def test_behind_socks5_the_srflx_is_the_exit(binary: Path, internet: Any, server: str) -> None:
    _, stun, proxy_url, proxy = internet
    got = gather(binary, server, capture=False, proxy={"server": proxy_url})
    assert stun.requests and {ip for ip, _ in stun.requests} == {EXIT}, (stun.requests, got)
    assert all(found == stun.answered for found in srflx(got).values()), (srflx(got), stun.answered)
    # A name goes to the relay as a name (ATYP 3); a placeholder never leaves.
    named = server.startswith(f"stun:{STUN_NAME}")
    assert {(atyp, host) for atyp, host, _ in proxy.destinations} == (
        {(3, STUN_NAME)} if named else {(1, STUN_ADDR[0])}), proxy.destinations


def test_behind_socks5_a_capturing_page_finds_no_host_address(
    binary: Path, internet: Any, local_stun: Tuple[LocalStun, str]
) -> None:
    _, stun, proxy_url, _ = internet
    leak, leak_url = local_stun
    got = gather(binary, [f"stun:{STUN_ADDR[0]}:{STUN_ADDR[1]}", leak_url], capture=True,
                 proxy={"server": proxy_url})
    seen = json.dumps(got)
    assert any(f" {EXIT} " in c and " typ host " in c for c in got["candidates"]), got
    assert [ip for ip in host_addresses() if ip in seen] == [], seen
    assert stun.requests and leak.requests == [], (stun.requests, leak.requests)


def test_behind_an_http_proxy_nothing_is_sent(binary: Path, local_stun: Tuple[LocalStun, str]) -> None:
    leak, leak_url = local_stun
    got = gather(binary, leak_url, capture=True, proxy={"server": "http://127.0.0.1:1"})
    assert leak.requests == [], leak.requests
    assert all(found == set() for found in srflx(got).values()), got
    seen = json.dumps(got)
    assert [ip for ip in host_addresses() if ip in seen] == [], seen


def test_an_unproxied_launch_is_unchanged(binary: Path, local_stun: Tuple[LocalStun, str]) -> None:
    """The control for the checks above: they can see this machine's address and a leaked STUN request."""
    stun, url = local_stun
    got = gather(binary, url, capture=True)
    assert stun.requests and all(found == stun.answered for found in srflx(got).values()), (srflx(got), stun.answered)
    assert own_address() in json.dumps(got) and EXIT not in json.dumps(got), got
