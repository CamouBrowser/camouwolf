# tests/playwright/

Playwright tests of Camouwolf's own behaviour: things stock Chromium does not
do, so the upstream suite cannot cover them.

`python -m ci.run_playwright` copies every `test_*.py` here into the fetched upstream
checkout and runs it with the same plugin (`ci/pw_camouwolf_plugin.py`) and the
same fixtures: `browser`, `context` and `page` are launched through the
launcher against the binary under test, on the run's private display.

This is not a fork of Playwright's tests. A deliberate difference from upstream
goes in `ci/skiplist.yml` with its reason; nothing is copied or patched here.
There are no tests in this directory yet.
