"""Input that Playwright drives reaches the page as a person's input does.

0032, 0033 and 0034 in docs/PATCHES.md.
"""

from playwright.async_api import Page

# Pixels a wheel tick scrolls in Chromium's native event builders, per OS.
PIXELS_PER_TICK = {"Win32": 100, "MacIntel": 40}
LINUX_PIXELS_PER_TICK = 120


async def test_wheel_delta_counts_the_claimed_os_ticks(page: Page) -> None:
    await page.set_content('<div style="width: 5000px; height: 5000px"></div>')
    await page.evaluate(
        """() => addEventListener('wheel', e => {
            window.lastWheel = {deltaY: e.deltaY, wheelDeltaY: e.wheelDeltaY};
        })"""
    )
    await page.mouse.move(50, 60)
    await page.mouse.wheel(0, 300)
    await page.wait_for_function("window.lastWheel")
    got = await page.evaluate("window.lastWheel")
    platform = await page.evaluate("navigator.platform")
    per_tick = PIXELS_PER_TICK.get(platform, LINUX_PIXELS_PER_TICK)
    assert got == {"deltaY": 300, "wheelDeltaY": -300 * 120 // per_tick}


async def test_a_drag_holds_the_left_button(page: Page) -> None:
    await page.set_content(
        """
        <div id="source" draggable="true" style="width: 100px; height: 100px">drag</div>
        <div id="target" style="width: 100px; height: 100px; margin-top: 50px">drop</div>
        <script>
          window.buttons = {};
          for (const type of ['drag', 'dragenter', 'dragover', 'drop', 'dragend'])
            addEventListener(type, e => {
              if (type !== 'dragend') e.preventDefault();
              window.buttons[type] = e.buttons;
            });
        </script>
        """
    )
    await page.drag_and_drop("#source", "#target")
    await page.wait_for_function("'dragend' in window.buttons")
    assert await page.evaluate("window.buttons") == {
        "drag": 1,
        "dragenter": 1,
        "dragover": 1,
        "drop": 1,
        "dragend": 0,
    }


async def test_a_choice_the_driver_makes_is_trusted(page: Page) -> None:
    await page.set_content(
        """
        <select><option>a</option><option>b</option></select>
        <input type="date">
        <script>
          window.trusted = [];
          for (const type of ['input', 'change'])
            addEventListener(type, e => window.trusted.push(
                [e.target.tagName + '.' + type, e.isTrusted]), true);
        </script>
        """
    )
    await page.select_option("select", "b")
    await page.fill("input", "2024-05-06")
    assert await page.evaluate("window.trusted") == [
        ["SELECT.input", True],
        ["SELECT.change", True],
        ["INPUT.input", True],
        ["INPUT.change", True],
    ]


async def test_an_event_the_page_or_test_dispatches_stays_untrusted(page: Page) -> None:
    await page.set_content("<button>b</button>")
    await page.evaluate(
        """() => {
            window.trusted = [];
            addEventListener('click', e => window.trusted.push(e.isTrusted));
            document.querySelector('button').dispatchEvent(new MouseEvent('click', {bubbles: true}));
        }"""
    )
    await page.dispatch_event("button", "click")
    assert await page.evaluate("window.trusted") == [False, False]
