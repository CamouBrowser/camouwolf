"""Smoke test: launch a Kraft build through the Camouwolf launcher.

Kraft is the Chromium fork Camouwolf's patches come from; its binary reads the
same config under KRAFT_CONFIG_* where a Camouwolf build reads CAMOU_CONFIG_*.
The launcher is run unchanged and its environment duplicated under Kraft's
names, so what is checked is the launcher's output against a real browser.

    ~/.cache/camouwolf-venv/bin/python python/tests/manual/smoke_kraft.py \
        [--binary PATH] [--headless true|virtual] [--os windows|linux]

Never opens a window on the real desktop: DISPLAY and WAYLAND_DISPLAY are
removed, and headless='virtual' runs on an Xvfb this script's launch starts.
Exits non-zero when a check fails.
"""

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

DEFAULT_BINARY = Path.home() / 'jobharvest/Kraft/browser-cache/kraft-152.0.7977.42-alpha.1/kraft'

# navigator.userAgentData and navigator.deviceMemory exist only in a secure
# context, which a data: URL is not; the same document is also served, by
# request interception, from an https origin.
SECURE_URL = 'https://smoke.camouwolf.test/'

DOCUMENT = (
    '<!doctype html><title>smoke</title>'
    '<button id=b style="position:absolute;left:300px;top:200px;width:160px;height:60px" '
    'onclick="window.clicked=(window.clicked||0)+1">go</button>'
    '<script>window.moves=0;window.trusted=null;'
    'addEventListener("mousemove",()=>{window.moves++});'
    'addEventListener("click",e=>{window.trusted=e.isTrusted})</script>'
)
PAGE = 'data:text/html,' + DOCUMENT

PROBE = """async () => {
  const out = {};
  out.userAgent = navigator.userAgent;
  out.platform = navigator.platform;
  out.webdriver = navigator.webdriver;
  out.hardwareConcurrency = navigator.hardwareConcurrency;
  out.deviceMemory = navigator.deviceMemory;
  out.languages = navigator.languages;
  out.language = navigator.language;
  out.intlLocale = Intl.DateTimeFormat().resolvedOptions().locale;
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.uad = navigator.userAgentData ? {
    platform: navigator.userAgentData.platform,
    mobile: navigator.userAgentData.mobile,
    brands: navigator.userAgentData.brands,
    high: await navigator.userAgentData.getHighEntropyValues(
      ['architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion', 'wow64', 'fullVersionList']),
  } : null;
  out.screen = {width: screen.width, height: screen.height, availWidth: screen.availWidth,
                availHeight: screen.availHeight, colorDepth: screen.colorDepth};
  out.window = {outerWidth, outerHeight, innerWidth, innerHeight, screenX, screenY,
                devicePixelRatio};
  const gl = document.createElement('canvas').getContext('webgl');
  if (gl) {
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    out.webgl = {vendor: ext && gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
                 renderer: ext && gl.getParameter(ext.UNMASKED_RENDERER_WEBGL),
                 maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE)};
  } else { out.webgl = null; }
  out.workerUA = await new Promise((resolve) => {
    try {
      const w = new Worker(URL.createObjectURL(new Blob(
        ['postMessage([navigator.userAgent, navigator.platform, navigator.hardwareConcurrency])'])));
      w.onmessage = (e) => resolve(e.data);
      w.onerror = () => resolve('worker error');
      setTimeout(() => resolve('worker timeout'), 5000);
    } catch (e) { resolve(String(e)); }
  });
  return out;
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--binary', default=str(DEFAULT_BINARY))
    parser.add_argument('--headless', default='true', choices=['true', 'virtual'])
    parser.add_argument('--os', default='windows', choices=['windows', 'linux'])
    parser.add_argument('--no-tz-env', action='store_true', help='drop TZ: test the config key alone')
    parser.add_argument('--json', action='store_true', help='print what the page reported')
    opts = parser.parse_args()

    # Nothing here may reach the real desktop.
    os.environ.pop('DISPLAY', None)
    os.environ.pop('WAYLAND_DISPLAY', None)
    warnings.simplefilter('ignore')

    from camouwolf import DefaultAddons, launch_options
    from camouwolf.sync_api import Camouwolf
    from camouwolf.utils import launch_config
    from camoucore.virtdisplay import VirtualDisplay
    from camouwolf.product import PRODUCT

    headless = True if opts.headless == 'true' else 'virtual'
    display = None
    extra = {}
    if headless == 'virtual':
        display = VirtualDisplay(PRODUCT)
        extra = {'virtual_display': display.get(), 'virtual_display_size': display.size}

    results = []

    def check(name: str, ok: bool, detail: str = '') -> None:
        results.append((name, bool(ok), detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + detail if detail and not ok else ''}")

    try:
        options = launch_options(
            executable_path=opts.binary,
            os=opts.os,
            headless=headless is True,
            locale='en-US',
            config={'timezone': 'America/Chicago'},
            exclude_addons=[DefaultAddons.UBO],
            i_know_what_im_doing=True,
            **extra,
        )
        # The Kraft binary's names for the same contract.
        for key, value in list(options['env'].items()):
            if key.startswith('CAMOU_CONFIG_'):
                options['env']['KRAFT_CONFIG_' + key[len('CAMOU_CONFIG_'):]] = value
            if key == 'CAMOUWOLF_UCRT_MATH':
                options['env']['KRAFT_UCRT_MATH'] = value
        if opts.no_tz_env:
            options['env'].pop('TZ', None)
        claimed = launch_config(options)

        check('identity is in the environment, not argv',
              not any('Mozilla/5.0' in arg or 'CAMOU' in arg for arg in options['args']))

        with Camouwolf(from_options=options, humanize=True) as browser:
            page = browser.new_page()
            page.goto(PAGE)
            on_data_url = page.evaluate(PROBE)
            check('data: URL loads and reports the identity',
                  on_data_url['userAgent'] == claimed['navigator.userAgent']
                  and on_data_url['platform'] == claimed['navigator.platform'], on_data_url['userAgent'])
            page.route(SECURE_URL, lambda route: route.fulfill(content_type='text/html', body=DOCUMENT))
            page.goto(SECURE_URL)
            seen = page.evaluate(PROBE)
            if opts.json:
                print(json.dumps(seen, indent=2))

            check('navigator.userAgent', seen['userAgent'] == claimed['navigator.userAgent'], seen['userAgent'])
            check('navigator.platform', seen['platform'] == claimed['navigator.platform'], seen['platform'])
            check('navigator.webdriver is false', seen['webdriver'] is False, repr(seen['webdriver']))
            check('navigator.hardwareConcurrency',
                  seen['hardwareConcurrency'] == claimed['navigator.hardwareConcurrency'],
                  str(seen['hardwareConcurrency']))
            check('navigator.deviceMemory', seen['deviceMemory'] == claimed['navigator.deviceMemory'],
                  str(seen['deviceMemory']))
            check('worker sees the same identity',
                  seen['workerUA'] == [claimed['navigator.userAgent'], claimed['navigator.platform'],
                                       claimed['navigator.hardwareConcurrency']], str(seen['workerUA']))

            uad = seen['uad'] or {}
            high = uad.get('high') or {}
            check('userAgentData.platform', uad.get('platform') == claimed['userAgentData.platform'],
                  str(uad.get('platform')))
            check('userAgentData.mobile', uad.get('mobile') == claimed['userAgentData.mobile'])
            for key in ('architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion', 'wow64'):
                check(f'userAgentData.{key}', high.get(key) == claimed[f'userAgentData.{key}'],
                      f'{high.get(key)!r} != {claimed[f"userAgentData.{key}"]!r}')
            milestone = claimed['userAgentData.uaFullVersion'].split('.')[0]
            brands = {b['brand']: b['version'] for b in uad.get('brands') or []}
            check('userAgentData.brands carry the milestone', brands.get('Chromium') == milestone, str(brands))

            for key in ('width', 'height', 'availWidth', 'availHeight', 'colorDepth'):
                check(f'screen.{key}', seen['screen'][key] == claimed[f'screen.{key}'],
                      f'{seen["screen"][key]} != {claimed[f"screen.{key}"]}')
            check('window.outerWidth/outerHeight (launcher: --window-size)',
                  (seen['window']['outerWidth'], seen['window']['outerHeight'])
                  == (claimed['window.outerWidth'], claimed['window.outerHeight']),
                  f'{seen["window"]} != {claimed["window.outerWidth"]}x{claimed["window.outerHeight"]}')

            webgl = seen['webgl'] or {}
            check('WebGL vendor', webgl.get('vendor') == claimed['webGl:vendor'], str(webgl.get('vendor')))
            check('WebGL renderer', webgl.get('renderer') == claimed['webGl:renderer'], str(webgl.get('renderer')))
            max_texture = claimed.get('webGl:parameters', {}).get('3379')
            if max_texture is not None:
                check('WebGL MAX_TEXTURE_SIZE (webGl:parameters)', webgl.get('maxTextureSize') == max_texture,
                      f'{webgl.get("maxTextureSize")} != {max_texture}')

            check('timezone', seen['timezone'] == claimed['timezone'], seen['timezone'])
            check('navigator.languages', seen['languages'] == claimed['navigator.languages'], str(seen['languages']))
            check('Intl locale (launcher: --lang)', seen['intlLocale'] == claimed['navigator.languages'][0],
                  seen['intlLocale'])

            check('"mw:" evaluate reaches the main world', page.evaluate('mw:document.title') == 'smoke')

            page.click('#b')
            state = page.evaluate('({clicked: window.clicked, moves: window.moves, trusted: window.trusted})')
            check('humanized click lands', state['clicked'] == 1, str(state))
            check('humanized click is trusted', state['trusted'] is True, str(state))
            check('humanized click moves the pointer along a path', state['moves'] >= 3, str(state))
    finally:
        if display:
            display.kill()

    failed = [name for name, ok, _ in results if not ok]
    print(f'\n{len(results) - len(failed)}/{len(results)} checks passed' + (f'; failed: {failed}' if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
