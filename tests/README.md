# tests/

The suites that need a browser or check the repository as a whole. The
launchers' own unit tests live beside them (`python/tests`, `typescript/tests`).
Every suite here is run through its stage in [`ci/`](../ci/README.md), which
supplies the binary, the private X display and the result file; none is meant
to be started by hand with a bare `pytest`.

| Path | Stage | What it checks |
|---|---|---|
| `build-tester/` | `python -m ci.run_build_tester` | Launches several drawn identities through the launcher and compares what a page reads -- navigator, client hints and their request headers, workers, timezone and locale, WebGL, screen, fonts, voices -- against what the launcher claimed. Also page-side checks for automation, tampering and headless tells. Policy in `ci/build-tester.yml`. |
| `native/` | `python -m ci.run_native` | `test_tribal_rules.py`: the decisions in `ci/tribal-rules.yml` (no browser). `test_no_leaks.py`, `test_crash_recovery.py`: no process or profile directory outlives a launch; a killed browser is survivable. |
| `playwright/` | `python -m ci.run_playwright` | Camouwolf's own Playwright tests. The upstream suite is fetched at run time and is not copied here. |

Functionality and stealth fail independently. A patch can spoof a surface and
break ordinary web behaviour, which the upstream Playwright suite catches; or
it can leave every Playwright test passing and spoof nothing, most often
because the launcher writes a config key no patch reads. The build-tester
catches that, by comparing what the browser reports with what was claimed.
