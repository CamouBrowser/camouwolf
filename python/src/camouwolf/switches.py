"""Chromium command-line hygiene.

Camoufox has no counterpart: Firefox's automation surface is Juggler, compiled
in. Chromium's is the command line, which holds three kinds of problem:

1. Switches that mark the process as automated (`--enable-automation`, which
   Playwright adds to every launch).
2. Switches that change behaviour a page can measure (`--disable-web-security`).
3. Switches whose value contradicts the identity. `--user-agent` moves
   navigator.userAgent without the client hints (see camouwolf.clienthints),
   so it is refused and the caller pointed at `config`.

The identity itself travels in the environment (CAMOU_CONFIG_*), never in argv:
argv is readable in /proc/<pid>/cmdline and `ps`, and Chromium copies much of it
to its renderers' command lines.

Ported from Kraft's src/switches.ts.
"""

from typing import Dict, Iterable, List, NamedTuple, Tuple

# Switches Playwright and Puppeteer add that mark the browser as automated.
# Dropped from the caller's args.
AUTOMATION_SWITCHES = frozenset(
    {
        '--enable-automation',
        '--enable-blink-features=IdleDetection',
        '--remote-debugging-pipe-log',
        '--test-type',
        '--no-service-autorun',
        '--password-store=basic',
        '--use-mock-keychain',
    }
)

# Playwright's own default arguments that are left out of the launch
# (`ignore_default_args`). --enable-automation drives the "controlled by
# automated test software" infobar and upstream's navigator.webdriver;
# --disable-extensions would stop `addons` from loading; and
# --disable-popup-blocking lets a page open a window without a user gesture,
# which stock Chrome blocks and a page can test.
IGNORED_DEFAULT_ARGS: Tuple[str, ...] = (
    '--enable-automation',
    '--disable-extensions',
    '--disable-popup-blocking',
)

# The features Playwright's Chromium launch disables (playwright-core
# chromiumSwitches.ts, 1.59-1.62). Chromium reads only the last
# --disable-features on a command line, so when the launcher needs one of its
# own (extensions), it restates this list with it rather than silently turning
# Playwright's back on.
PLAYWRIGHT_DISABLED_FEATURES: Tuple[str, ...] = (
    'AvoidUnnecessaryBeforeUnloadCheckSync',
    'BoundaryEventDispatchTracksNodeRemoval',
    'DestroyProfileOnBrowserClose',
    'DialMediaRouteProvider',
    'GlobalMediaControls',
    'HttpsUpgrades',
    'LensOverlay',
    'MediaRouter',
    'PaintHolding',
    'ThirdPartyStoragePartitioning',
    'BlockOriginHeaderModificationOnRedirect',
    'Translate',
    'AutoDeElevate',
    'OptimizationHints',
    'msForceBrowserSignIn',
    'msEdgeUpdateLaunchServicesPreferredVersion',
)

# Switches that change web-visible behaviour: cross-origin reads without CORS,
# no site isolation, and so on. A page can test for each directly.
BEHAVIOR_CHANGING_SWITCHES = frozenset(
    {
        '--disable-web-security',
        '--disable-site-isolation-trials',
        '--disable-features=IsolateOrigins,site-per-process',
        '--allow-running-insecure-content',
        '--disable-popup-blocking',
        '--ignore-certificate-errors',
    }
)

# Switches whose value would contradict the CAMOU_CONFIG identity, with the
# option to use instead. Refused rather than dropped: a caller passing
# --user-agent believes the user agent is set.
CONFLICTING_SWITCHES: Dict[str, str] = {
    '--user-agent': (
        'Setting --user-agent moves navigator.userAgent but not the Sec-CH-UA-* client hints, '
        'so the two disagree on every request. Pass the identity through `config` or '
        '`fingerprint` instead; Camouwolf derives the user agent and the hints from it together.'
    ),
    '--lang': (
        'Setting --lang moves the UI language but not navigator.languages or the '
        'Accept-Language header. Use the `locale` option, which sets all three.'
    ),
    '--accept-lang': (
        'Setting --accept-lang moves the Accept-Language header but not the UI language or '
        'navigator.languages. Use the `locale` option, which sets all three.'
    ),
    '--window-size': (
        'Setting --window-size moves the real window without the screen the identity claims. '
        'Use the `window` option.'
    ),
    '--window-position': (
        'Setting --window-position moves the real window without the screen the identity '
        'claims. Set window.screenX and window.screenY in `config`.'
    ),
    '--force-device-scale-factor': (
        "Setting the device scale factor here separates it from the identity's screen. "
        'Use the `screen` option.'
    ),
    '--proxy-server': (
        'Use the `proxy` option, which also sends the geoip lookup through the proxy.'
    ),
    '--headless': (
        "Use the `headless` option. The switch bypasses headless='virtual' and 'virtual-gpu'."
    ),
    '--user-data-dir': (
        'Use `persistent_context=True` with `user_data_dir`.'
    ),
}

# Switches every Camouwolf launch adds. Each removes something a fresh automated
# profile shows and a person's does not, or a request to Google a datacenter
# launch should not make. Nothing here changes rendering or what a page can do.
BASE_SWITCHES: Tuple[str, ...] = (
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-domain-reliability',
    '--metrics-recording-only',
    '--disable-breakpad',
    '--disable-blink-features=AutomationControlled',
)

# Switches whose value is a comma-separated list. Chromium reads only the last
# occurrence of a repeated switch, so several are merged into one.
LIST_SWITCHES = frozenset(
    {'--enable-features', '--disable-features', '--enable-blink-features', '--disable-blink-features',
     '--load-extension'}
)


class SwitchAudit(NamedTuple):
    #: The arguments to launch with.
    args: List[str]
    #: (argument, reason) for each one dropped.
    dropped: List[Tuple[str, str]]


def flag_name(arg: str) -> str:
    return arg.split('=', 1)[0]


def merge_list_switches(args: Iterable[str]) -> List[str]:
    """Merge repeated list-valued switches (--disable-features=A and
    --disable-features=B) into one, at the position of the first."""
    merged: List[str] = []
    position: Dict[str, int] = {}
    for arg in args:
        name = flag_name(arg)
        if name not in LIST_SWITCHES or '=' not in arg:
            merged.append(arg)
            continue
        values = [v for v in arg.split('=', 1)[1].split(',') if v]
        if name in position:
            current = merged[position[name]].split('=', 1)[1].split(',')
            merged[position[name]] = f'{name}=' + ','.join(
                current + [v for v in values if v not in current]
            )
        else:
            position[name] = len(merged)
            merged.append(f'{name}=' + ','.join(dict.fromkeys(values)))
    return merged


def audit_switches(args: Iterable[str], strict: bool = True) -> SwitchAudit:
    """Filter the caller's arguments and add the base switches.

    `strict` raises ValueError on a switch that conflicts with the identity
    instead of dropping it.
    """
    dropped: List[Tuple[str, str]] = []
    kept: List[str] = []

    for arg in args:
        name = flag_name(arg)
        conflict = CONFLICTING_SWITCHES.get(name)
        if conflict:
            if strict:
                raise ValueError(f"Camouwolf refuses '{name}'. {conflict}")
            dropped.append((arg, conflict))
        elif arg in AUTOMATION_SWITCHES or name in AUTOMATION_SWITCHES:
            dropped.append((arg, 'automation marker'))
        elif arg in BEHAVIOR_CHANGING_SWITCHES or name in BEHAVIOR_CHANGING_SWITCHES:
            dropped.append((arg, 'changes web-visible behavior'))
        else:
            kept.append(arg)

    # Base switches first; a caller's list-valued switch of the same name is
    # merged with it, and any other repeat is the caller's (Chromium reads the last).
    seen = {flag_name(arg) for arg in kept if flag_name(arg) not in LIST_SWITCHES}
    base = [s for s in BASE_SWITCHES if flag_name(s) not in seen]
    return SwitchAudit(merge_list_switches([*base, *kept]), dropped)
