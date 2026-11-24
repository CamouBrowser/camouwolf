#!/usr/bin/env python3
"""A local HTTP(S) forward proxy that caps total download speed.

`gclient sync` pulls tens of gigabytes through git, cipd and gsutil, and none
of them has a bandwidth limit. All three honour `https_proxy`, so build.sh
points them at this proxy when CAMOUWOLF_RATE_LIMIT_MB is set and the cap then
holds for the whole sync, whatever tool is downloading.

Only the download direction (server to client) is limited; uploads are
requests and are small. The limit is one token bucket shared by every
connection.

Usage:  ratecap-proxy.py --mb-per-s 10 [--port 0] [--port-file PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import time
from urllib.parse import urlsplit

CHUNK = 64 * 1024


class Bucket:
    """Token bucket shared by all connections. Holds at most 0.25 s of data."""

    def __init__(self, rate: float) -> None:
        self.rate = rate
        self.capacity = max(rate / 4, CHUNK)
        self.tokens = self.capacity
        self.stamp = time.monotonic()
        self.lock = asyncio.Lock()

    async def take(self, n: int) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.rate)
                self.stamp = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                await asyncio.sleep((n - self.tokens) / self.rate)


async def pump(reader, writer, bucket: Bucket | None) -> None:
    try:
        while data := await reader.read(CHUNK):
            if bucket:
                await bucket.take(len(data))
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def handle(client_r, client_w, bucket: Bucket) -> None:
    try:
        head = await client_r.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        client_w.close()
        return
    request_line, _, rest = head.partition(b"\r\n")
    try:
        method, target, version = request_line.decode("latin-1").split(" ", 2)
    except ValueError:
        client_w.close()
        return

    try:
        if method == "CONNECT":
            host, _, port = target.rpartition(":")
            upstream_r, upstream_w = await asyncio.open_connection(host.strip("[]"), int(port))
            client_w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await client_w.drain()
        else:
            url = urlsplit(target)
            upstream_r, upstream_w = await asyncio.open_connection(url.hostname, url.port or 80)
            path = url.path or "/"
            if url.query:
                path += "?" + url.query
            # One request per connection, so a keep-alive client cannot send a
            # second request to a different host down the same upstream socket.
            headers = [
                line
                for line in rest.split(b"\r\n")
                if line and not line.lower().startswith((b"proxy-connection:", b"connection:"))
            ]
            upstream_w.write(
                f"{method} {path} {version}\r\n".encode("latin-1")
                + b"\r\n".join(headers)
                + b"\r\nConnection: close\r\n\r\n"
            )
            await upstream_w.drain()
    except (OSError, ValueError):
        client_w.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        client_w.close()
        return

    await asyncio.gather(
        pump(client_r, upstream_w, None),
        pump(upstream_r, client_w, bucket),
    )


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mb-per-s", type=float, required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--port-file", help="write the listening port here once bound")
    args = ap.parse_args()

    bucket = Bucket(args.mb_per_s * 1024 * 1024)
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, bucket), "127.0.0.1", args.port, limit=1024 * 1024
    )
    port = server.sockets[0].getsockname()[1]
    if args.port_file:
        with open(args.port_file, "w") as f:
            f.write(str(port))
    print(f"ratecap-proxy: 127.0.0.1:{port}, {args.mb_per_s:g} MB/s", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
