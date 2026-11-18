#!/usr/bin/env python3
"""Work out which browser this run tests, and which Playwright suite tests it.

Called by every entry point, so every run agrees on what "the tests" means:

  pull request      browser comes from upstream.sh; suite is whichever released
                    playwright-python tag targets that Chromium milestone.
  another build     `--browser-version` names the Chromium it was built from,
                    and the same resolution runs against that instead.

Suite selection is "newest released tag whose pinned Chromium is not ahead of
ours, and which is below the Playwright ceiling in python/pyproject.toml".
Playwright pins a Chromium a few milestones ahead of stable, so requiring an
exact match would mean no suite at all for most of a release cycle; taking a
newer suite than the browser would mean testing against a CDP contract this
build does not have. Newest-not-ahead is the one that is both available and
honest.

Run:
    python3 -m ci.versions --json
    python3 -m ci.versions --browser-version 152.0.7977.42 --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict, List, Optional, Tuple

from ._util import (
    REPO_ROOT,
    gh_headers,
    http_json,
    log,
    major,
    parse_version,
    read_upstream_sh,
    set_output,
)

BROWSERS_JSON = "https://raw.githubusercontent.com/microsoft/playwright/{ref}/packages/playwright-core/browsers.json"
TAGS_API = "https://api.github.com/repos/microsoft/playwright-python/tags?per_page=100"
PYPROJECT = "python/pyproject.toml"
_CEILING = re.compile(r'^playwright\s*=\s*"<\s*([0-9][0-9.]*)"', re.M)

# Consulted only when the network is unavailable or GitHub is rate-limiting an
# unauthenticated runner. Deliberately short: it is a floor, not a source of
# truth, and a stale entry here is better than a run that cannot start.
FALLBACK_PINS: Tuple[Tuple[str, str], ...] = (
    ("v1.62.0", "151.0.7922.34"),
    ("v1.61.0", "149.0.7827.55"),
    ("v1.60.0", "148.0.7778.96"),
    ("v1.59.0", "147.0.7727.15"),
    ("v1.58.0", "145.0.7632.6"),
)


def released_tags(limit: int = 40) -> List[str]:
    """Final playwright-python tags, newest first."""
    tags = http_json(TAGS_API, headers=gh_headers())
    parsed: List[Tuple[Tuple[int, int, int], str]] = []
    for tag in tags:
        name = tag.get("name", "")
        if not name.startswith("v") or any(m in name for m in ("alpha", "beta", "rc", "next", "-")):
            continue
        try:
            parsed.append((parse_version(name[1:]), name))
        except ValueError:
            continue
    parsed.sort(reverse=True)
    return [name for _, name in parsed[:limit]]


def chromium_pinned_by(tag: str) -> Optional[str]:
    """The Chromium browserVersion a Playwright tag ships against."""
    try:
        data = http_json(BROWSERS_JSON.format(ref=tag))
    except Exception:
        return None
    for browser in data.get("browsers", []):
        if browser.get("name") == "chromium":
            return browser.get("browserVersion")
    return None


def pins(limit: int = 10) -> List[Tuple[str, str]]:
    """[(playwright tag, chromium version)], newest first, with a fallback."""
    try:
        out = []
        for tag in released_tags()[:limit]:
            chromium = chromium_pinned_by(tag)
            if chromium:
                out.append((tag, chromium))
        if out:
            return out
        log("no Playwright pins resolved from the network", level="WARN")
    except Exception as exc:  # noqa: BLE001
        log(f"could not reach the Playwright tag list ({exc}); using the built-in table", level="WARN")
    return list(FALLBACK_PINS)


def client_ceiling() -> Optional[Tuple[int, int, int]]:
    """The Playwright version the Python launcher refuses to go to, or None if unpinned.

    `camouwolf.server` imports `playwright._impl._driver`, a private API with no
    compatibility guarantee, and `camouwolf.switches` restates the switches of
    Playwright's Chromium launch, which every Playwright minor is free to
    change. The Python launcher pins a ceiling for that reason, and a suite run above it
    would be testing the browser against a client its own package will not
    install -- a green run that proves nothing a user can reproduce.
    """
    try:
        text = (REPO_ROOT / PYPROJECT).read_text(encoding="utf-8")
    except OSError:
        return None
    found = _CEILING.search(text)
    if not found:
        return None
    try:
        return parse_version(found.group(1))
    except ValueError:
        return None


def resolve(
    *,
    browser_version: Optional[str] = None,
    playwright_tag: Optional[str] = None,
) -> Dict[str, str]:
    """Everything a run needs to know about versions."""
    upstream = read_upstream_sh()
    browser_version = browser_version or upstream.get("version", "")
    release = upstream.get("release", "")
    if not browser_version:
        raise SystemExit("could not determine a browser version; upstream.sh has no `version`")

    available = pins()

    if playwright_tag:
        pinned = dict(available).get(playwright_tag) or chromium_pinned_by(playwright_tag) or ""
        chosen = (playwright_tag, pinned)
        note = f"pinned explicitly to {playwright_tag}, which targets Chromium {pinned or '?'}"
    else:
        ours = major(browser_version)
        # Newest suite that is not ahead of the browser we are testing.
        eligible = [(t, f) for t, f in available if major(f) <= ours]

        # ... and not above the client ceiling the Python launcher pins. Dropping this
        # filter does not fail loudly: the suite installs a client the shipped
        # package forbids, and whatever the driver changed in between reads as a
        # browser bug.
        ceiling = client_ceiling()
        if ceiling and eligible:
            allowed = [(t, f) for t, f in eligible if parse_version(t.lstrip("v")) < ceiling]
            if allowed and allowed != eligible:
                dropped = sorted({t for t, _ in eligible} - {t for t, _ in allowed})
                log(
                    f"ignoring {', '.join(dropped)}: at or above the Python launcher's "
                    f"playwright ceiling ({PYPROJECT})"
                )
                eligible = allowed
            elif not allowed:
                log(
                    f"every suite not ahead of Chromium {ours} is at or above the Python launcher's "
                    f"playwright ceiling; testing above it. Bump the ceiling in {PYPROJECT} "
                    "or expect protocol noise.",
                    level="WARN",
                )

        if eligible:
            chosen = max(eligible, key=lambda tf: parse_version(tf[1]))
            note = (
                f"the newest released suite not ahead of Chromium {ours} "
                f"(it targets Chromium {chosen[1]})"
            )
        else:
            # Every known suite is newer than this browser -- an old branch, or
            # a Chromium so new nothing targets it yet. Take the oldest available
            # rather than refusing to test at all, and say so loudly.
            chosen = min(available, key=lambda tf: parse_version(tf[1]))
            note = (
                f"no suite targets Chromium {ours} or older; falling back to the oldest "
                f"available ({chosen[0]}, Chromium {chosen[1]}). Expect conformance noise."
            )
            log(note, level="WARN")

    ours_display = major(browser_version)
    resolved = {
        "browser_version": browser_version,
        "browser_release": release,
        "playwright_tag": chosen[0],
        "playwright_chromium": chosen[1],
        "note": note,
    }
    log(
        f"testing Camouwolf {browser_version} ({release or 'no release tag'}), built on "
        f"Chromium {ours_display}, against Playwright {chosen[0]} -- {note}"
    )
    return resolved


def upstream_mismatch(browser_version: Optional[str]) -> Optional[str]:
    """Complain if a requested version is not the one the build will produce.

    Only suite SELECTION follows `--browser-version`. The build does not: both
    `ci.run_build` and `make` read upstream.sh, and the fetch path downloads
    whatever the Python launcher considers current. So asking for a version the branch
    does not pin gets you the old browser tested against the new suite -- the
    exact inversion of the intent, and silent.

    The legitimate flow has no mismatch in it: a Chromium bump edits upstream.sh
    on its branch, and resolution then reads that by default. This exists to
    catch the other case before it produces a meaningless green run.
    """
    if not browser_version:
        return None
    pinned = (read_upstream_sh().get("version") or "").strip()
    if not pinned or pinned == browser_version.strip():
        return None
    return (
        f"asked to test Chromium {browser_version}, but upstream.sh pins {pinned}. "
        "The build follows upstream.sh, so this would compile "
        f"{pinned} and judge it against the suite chosen for {browser_version}. "
        "Bump upstream.sh on the branch instead -- that is what an upgrade is."
    )


def fetched_mismatch(fetched: str, expected: str) -> Optional[str]:
    """The published build must be the Chromium generation the suite was chosen for.

    The fetch path does not build; it downloads the current release, which
    is the right browser to judge a launcher change against -- it is what users
    run. But the suite comes from upstream.sh, and during an upgrade window those
    two part company: upstream.sh moves to the new Chromium before any build of it
    is published, so a launcher-only pull request would fetch the OLD browser and test it against
    the NEW suite.

    Release drift inside a milestone is fine and expected (alpha.1 vs alpha.2 does
    not change which Playwright tag is right). A generation apart is not.
    """
    found = re.search(r"(\d+(?:\.\d+)*)", fetched or "")
    if not found:
        return f"could not read a version out of the fetched build: {fetched!r}"
    try:
        got, want = major(found.group(1)), major(expected)
    except ValueError:
        return f"could not compare fetched {fetched!r} with expected {expected!r}"
    if got == want:
        return None
    return (
        f"fetched Camouwolf {found.group(1)} (Chromium {got}), but the suite was chosen "
        f"for Chromium {want}. A launcher-only run tests the published release, and no "
        f"release of Chromium {want} is published yet. Wait for one, or let this run "
        "build from source instead."
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-version", help="override the version from upstream.sh")
    parser.add_argument("--playwright-tag", help="pin the suite instead of resolving one")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--check-fetched",
        metavar="VERSION",
        help="fail if this fetched build is a different Chromium generation than the suite",
    )
    parser.add_argument(
        "--check-upstream",
        action="store_true",
        help="fail if --browser-version disagrees with upstream.sh",
    )
    args = parser.parse_args(argv)

    if args.check_fetched:
        upstream = read_upstream_sh()
        expected = args.browser_version or upstream.get("version", "")
        problem = fetched_mismatch(args.check_fetched, expected)
        if problem:
            log(problem, level="ERROR")
            return 1
        log(f"fetched build agrees with the suite's Chromium generation ({major(expected)})")
        return 0

    if args.check_upstream:
        problem = upstream_mismatch(args.browser_version)
        if problem:
            log(problem, level="ERROR")
            return 1

    resolved = resolve(
        browser_version=args.browser_version, playwright_tag=args.playwright_tag
    )
    if args.json:
        print(json.dumps(resolved, indent=2, sort_keys=True))
    for key, value in resolved.items():
        set_output(key, value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
