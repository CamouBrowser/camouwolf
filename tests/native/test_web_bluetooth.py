"""Web Bluetooth answers as a machine without an adapter, whatever the host has.

A Windows or macOS identity exposes `navigator.bluetooth` (0019). If its answers
came from the host, a host with a radio would report `availability: true` and
open a real device chooser: the host's hardware, read by any page. A runner has
no adapter, so the host here is a fake BlueZ with one powered adapter, on a
private system bus the browser is pointed at.
"""

import asyncio
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest
from dbus_fast import MessageType, PropertyAccess
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property
from playwright.sync_api import sync_playwright

from _procs import options

ADAPTER = "/org/bluez/hci0"
NO_ADAPTER = {"availability": "false", "requestDevice": "NotFoundError: Bluetooth adapter not available."}
PROBE = """async () => {
    const settle = (promise) => Promise.race([
        promise.then((value) => String(value), (error) => `${error.name}: ${error.message}`),
        new Promise((resolve) => setTimeout(() => resolve("still pending after 5s"), 5000)),
    ]);
    return {
        availability: await settle(navigator.bluetooth.getAvailability()),
        requestDevice: await settle(navigator.bluetooth.requestDevice({acceptAllDevices: true})),
    };
}"""


def _readonly(name: str, signature: str, value: Any) -> Any:
    def getter(self: ServiceInterface) -> Any:
        return value
    getter.__name__ = name
    getter.__annotations__["return"] = signature
    return dbus_property(access=PropertyAccess.READ)(getter)


class Adapter(ServiceInterface):
    """One powered org.bluez.Adapter1, as BlueZ reports a USB dongle.

    The bus library answers ObjectManager.GetManagedObjects with these properties.
    """

    def __init__(self) -> None:
        super().__init__("org.bluez.Adapter1")

    Address = _readonly("Address", "s", "00:1A:7D:DA:71:13")
    AddressType = _readonly("AddressType", "s", "public")
    Name = _readonly("Name", "s", "hci0")
    Alias = _readonly("Alias", "s", "hci0")
    Class = _readonly("Class", "u", 0x6C010C)
    Powered = _readonly("Powered", "b", True)
    Discoverable = _readonly("Discoverable", "b", False)
    Pairable = _readonly("Pairable", "b", True)
    Discovering = _readonly("Discovering", "b", False)
    UUIDs = _readonly("UUIDs", "as", [])


@pytest.fixture
def fake_bluez(tmp_path: Path) -> Iterator[Dict[str, Any]]:
    """A private bus that owns org.bluez and reports one powered adapter."""
    daemon = shutil.which("dbus-daemon")
    assert daemon, "dbus-daemon is not installed; the fake BlueZ needs a bus of its own"
    bus = subprocess.Popen([daemon, "--session", "--nofork", "--print-address=1",
                            f"--address=unix:path={tmp_path / 'system_bus'}"],
                           stdout=subprocess.PIPE, text=True)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        address = bus.stdout.readline().strip()
        assert address, f"dbus-daemon exited {bus.poll()} without an address"
        calls: List[str] = []

        async def serve() -> MessageBus:
            connection = await MessageBus(bus_address=address).connect()
            connection.add_message_handler(lambda message: calls.append(f"{message.interface}.{message.member}")
                                           if message.message_type == MessageType.METHOD_CALL else None)
            connection.export(ADAPTER, Adapter())
            await connection.request_name("org.bluez")
            return connection

        connection = asyncio.run_coroutine_threadsafe(serve(), loop).result(10)
        yield {"address": address, "calls": calls}
        loop.call_soon_threadsafe(connection.disconnect)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        bus.terminate()
        bus.wait(5)


def test_web_bluetooth_does_not_reach_the_hosts_adapter(binary: Path, fake_bluez: Dict[str, Any]) -> None:
    # A Linux host launches only Windows identities; macOS shares the browser side.
    launch = options(binary, os_name="windows")
    launch["env"]["DBUS_SYSTEM_BUS_ADDRESS"] = fake_bluez["address"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch)
        try:
            page = browser.new_page()
            # navigator.bluetooth exists only in a secure context.
            page.route("https://bluetooth.test/", lambda route: route.fulfill(body="<title>bt</title>",
                                                                              content_type="text/html"))
            page.goto("https://bluetooth.test/")
            answers = page.evaluate(PROBE)
        finally:
            browser.close()
    # The browser lists BlueZ's adapters at startup, Web Bluetooth or not: this
    # proves it ran on the host with an adapter, not on one without.
    assert "org.freedesktop.DBus.ObjectManager.GetManagedObjects" in fake_bluez["calls"], (
        f"the browser never listed the fake BlueZ's adapters ({fake_bluez['calls']}); the check proves nothing")
    assert answers == NO_ADAPTER, f"a Windows identity on a host with an adapter answered {answers}"
