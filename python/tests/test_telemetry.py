"""Telemetry: every context of a launch is watched, and it never breaks one."""

import asyncio

import pytest

from camouwolf import async_api, sync_api, utils


class Events:
    def __init__(self):
        self.handlers = {}
        self.pages = []

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, *args):
        for handler in self.handlers.get(event, []):
            handler(*args)


class Context(Events):
    def new_page(self):
        pass


class Page:
    def __init__(self, context):
        self.context = context


class Browser(Events):
    def __init__(self):
        super().__init__()
        self.contexts = []

    def new_context(self, **kwargs):
        context = Context()
        self.contexts.append(context)
        return context

    def new_page(self, **kwargs):
        # Playwright makes the page's context inside, not through the
        # (wrapped) public new_context().
        context = Context()
        self.contexts.append(context)
        return Page(context)


class Chromium:
    def __init__(self, browser):
        self.browser = browser

    def launch(self, **kwargs):
        return self.browser

    def launch_persistent_context(self, **kwargs):
        return self.browser


class Playwright:
    def __init__(self, browser):
        self.chromium = Chromium(browser)


@pytest.fixture
def made(monkeypatch):
    """Every Telemetry the launcher creates, with a fake sender."""
    made = []

    class Recording(utils.Telemetry):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, send=lambda url, payload: None, interval=0, **kwargs)
            self.watched = []
            made.append(self)

        def watch(self, context):
            self.watched.append(context)
            super().watch(context)

    monkeypatch.setattr(utils, 'Telemetry', Recording)
    monkeypatch.delenv('CAMOUWOLF_TELEMETRY')
    return made


def test_every_context_and_page_is_watched(options, made):
    browser = Browser()
    sync_api.NewBrowser(Playwright(browser), from_options=options(os='windows'), telemetry=True)

    context = browser.new_context()
    page = browser.new_page()

    (telemetry,) = made
    assert telemetry.watched == [context, page.context]
    assert browser.handlers['disconnected']
    assert telemetry.dimensions['product'] == 'camouwolf'
    assert telemetry.dimensions['pro'] is False
    assert telemetry.dimensions['passthrough'] is False
    assert telemetry.dimensions['proxy_provider'] == 'none'


def test_the_dimensions_say_cross_os_and_passthrough(options, made, monkeypatch):
    monkeypatch.setattr(utils, 'OS_NAME', 'lin')
    for launch in (options(os='windows'), options(os='linux'), options(passthrough=True)):
        sync_api.NewBrowser(Playwright(Browser()), from_options=launch, telemetry=True)

    assert [(t.dimensions['cross_os'], t.dimensions['passthrough']) for t in made] == [
        (True, False), (False, False), (False, True)]


def test_a_persistent_context_is_watched_and_closes_it(options, made):
    context = Browser()
    sync_api.NewBrowser(Playwright(context), from_options=options(os='linux'), persistent_context=True, telemetry=True)

    (telemetry,) = made
    assert telemetry.watched == [context]
    context.emit('close')
    assert telemetry._closed


def test_the_async_api_watches_awaited_contexts(options, made):
    class AsyncContext(Context):
        async def new_page(self):
            pass

    class AsyncBrowser(Browser):
        async def new_context(self, **kwargs):
            context = AsyncContext()
            self.contexts.append(context)
            return context

    class AsyncChromium(Chromium):
        async def launch(self, **kwargs):
            return self.browser

    browser = AsyncBrowser()
    playwright = Playwright(browser)
    playwright.chromium = AsyncChromium(browser)

    async def run():
        launched = await async_api.AsyncNewBrowser(playwright, from_options=options(os='linux'), telemetry=True)
        return await launched.new_context()

    context = asyncio.run(run())
    (telemetry,) = made
    assert telemetry.watched == [context]


def test_off_creates_nothing(options, made, monkeypatch):
    sync_api.NewBrowser(Playwright(Browser()), from_options=options(os='linux'), telemetry=False)
    monkeypatch.setenv('CAMOUWOLF_TELEMETRY', '0')
    sync_api.NewBrowser(Playwright(Browser()), from_options=options(os='linux'), telemetry=True)
    assert made == []


def test_a_telemetry_failure_never_breaks_a_launch(options, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError('telemetry broke')

    monkeypatch.setattr(utils, 'Telemetry', broken)
    monkeypatch.delenv('CAMOUWOLF_TELEMETRY')
    browser = Browser()
    assert sync_api.NewBrowser(Playwright(browser), from_options=options(os='linux'), telemetry=True) is browser
    assert browser.new_context()
