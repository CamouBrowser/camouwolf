# Camouwolf

An anti-detect Chromium for web scraping and automation, and the launchers that
drive it. Camouwolf is the Chromium counterpart of
[Camoufox](https://github.com/daijro/camoufox): the same idea and the same
launcher API, on Chrome's engine.

A launch draws a whole device identity -- user agent and client hints, screen,
GPU, fonts, locale, timezone -- from a model of real-world traffic, checks that
its parts agree, and hands it to the browser. The browser answers with it from
C++, so a page sees one consistent machine and no injected script.

**Status: pre-release.** Nothing is published yet: there is no GitHub release,
and neither `camouwolf` package is on PyPI or npm. Only Linux x86_64 is built;
`python3 -m ci.targets` lists the other targets and why each is off.

## Layout

| Path | What it is |
|---|---|
| [`browser/`](browser/) | The patch series, the C++ config layer and the build scripts, pinned to a Chromium tag in `browser/upstream.sh`. Each patch is described in [`docs/PATCHES.md`](docs/PATCHES.md). |
| [`python/`](python/README.md) | The `camouwolf` Python package, the reference launcher. |
| [`typescript/`](typescript/) | The `camouwolf` npm package, a port of the Python launcher. |
| [`tests/`](tests/README.md) | Suites that need a built browser. |
| [`ci/`](ci/README.md) | The test pipeline, the same locally and in GitHub Actions. |

## Using it

```python
from camouwolf.sync_api import Camouwolf

with Camouwolf(os="windows", headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

Until there is a release, install the launcher from this tree
(`pip install ./python`) and name a browser you built with `executable_path=`.
Every option is in [`python/README.md`](python/README.md).

## Building the browser

Linux only. A cold build needs about 100 GB of disk and several hours; a warm
rebuild after a patch edit takes 10 to 20 minutes.

```bash
bash browser/scripts/build.sh                                   # sync Chromium at the pin, apply the series, compile
bash scripts/build-camouwolf-incremental.sh                     # warm tree: re-apply and rebuild what changed
bash scripts/release-camouwolf.sh --target linux --no-release   # package into browser/dist/
```

The header of `browser/scripts/build.sh` lists the environment variables that
cap CPU, disk and download rate. Changing a patch is described in
[`AGENTS.md`](AGENTS.md#changing-a-patch).

## Testing

```bash
pip install -r ci/requirements.txt ./python
python3 -m ci.run_patch_guards                         # the series against the pinned tag
python3 -m ci.run_pythonlib
python3 -m ci.run_typescript                           # Node 22.15 or newer
python3 -m ci.run_build_tester --binary <camouwolf>    # what a page reads vs what was claimed
python3 -m ci.run_playwright --binary <camouwolf>      # the upstream Playwright suite
```

Every stage, and how the workflows use them, is in [`ci/README.md`](ci/README.md).
Contribution rules are in [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`AGENTS.md`](AGENTS.md).

## License

See [`LICENSE.md`](LICENSE.md).
