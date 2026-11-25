# ci/

The whole test pipeline. Every stage is a Python module that runs the same way
on a laptop and in GitHub Actions: the workflows in `.github/workflows/` only
decide which machine runs `python -m ci.<stage>` and in what order.

## How a stage reports

A stage writes one JSON file, `<gate>.json`, into `.ci-work/results/`
(`$CI_RESULTS_DIR` overrides the directory) and exits non-zero when it fails.
`ci.summarize` reads the directory, merges the Playwright shards, and gives one
verdict:

```bash
python -m ci.summarize --require native_rules patch_guards pythonlib typescript
```

A suite named in `--require` that left no result file is a failure, not a skip.
`.ci-work/` ignores itself; nothing in it is committed.

## The binary

Browser stages take the browser from `--binary` or `$CAMOUWOLF_BINARY`. There is
no default path, and no stage builds one implicitly. Every launch goes through
the Python launcher in `python/` (`ci.browser.launcher_options`), on an Xvfb the
stage starts for itself (`ci.browser.private_display`), with
`--ozone-platform=x11` and `WAYLAND_DISPLAY` removed -- so no window can reach a
desktop, headless or not.

## Stages

| Stage | Gate | Needs a browser | What it runs |
|---|---|---|---|
| `ci.run_native --subset rules` | `native_rules` | no | `tests/native/test_tribal_rules.py`: every checkable decision in `ci/tribal-rules.yml` |
| `ci.run_patch_guards` | `patch_guards` | no | The patch series against the pinned Chromium tag: the series file is complete, every config key the browser reads is in the schema and named by the launcher (`scripts/check-config-keys.py`), the bundled fontconfig parses, branding is regenerated, every hunk applies (`scripts/check-dead-patches.py`), every API a patch calls exists (`scripts/check-patch-apis.py`). `--only <guard>`, `--offline` |
| `ci.run_pythonlib` | `pythonlib` | no | `python/tests` |
| `ci.run_typescript` | `typescript` | no | `typescript/`: frozen install, `tsc`, biome, build, the pack check, vitest. Needs Node 22.15 or newer and pnpm |
| `ci.run_prepare` | `prepare` | builds | `browser/scripts/build.sh` with `CAMOUWOLF_STAGES="sync patch"`; network failures are retried, a patch that does not apply is not |
| `ci.run_build` | `build` | builds | `build.sh`, then `browser/scripts/fetch-fonts.py --extract`, then `scripts/release-camouwolf.sh --skip-build --no-release`, then the archive's checksum |
| `ci.run_install` | `install` | yes | Unpacks the archive (or takes `--binary`), checks the layout and `version.json`, runs `--version`, launches once. Prints and outputs `binary` |
| `ci.run_skiplist_audit` | `skiplist_audit` | yes | Runs every `ci/skiplist.yml` entry with the skiplist empty; an entry that passes is stale and fails the audit |
| `ci.run_build_tester` | `build_tester` | yes | `tests/build-tester`: what a page reads against what the launcher claimed, over several drawn identities and locales (`ci/build-tester.yml`) |
| `ci.run_native --subset browser` | `native_browser` | yes | `tests/native`: no process or profile directory outlives a launch; a killed browser is survivable; a canvas reads back the same pixels from its first read on; WebRTC keeps off UDP when the launcher blocks it; a SOCKS5 proxy carries page and WebRTC traffic, with a password too; each behaviour launch option does what it says |
| `ci.run_playwright` | `playwright` (`playwright-NofM` per shard) | yes | The upstream playwright-python suite, launched through the launcher by `ci/pw_camouwolf_plugin.py`. `--shard 2/4`, `--only <path or node id>`, `--playwright-tag` |
| `ci.run_sundial` | `sundial` | yes | The stealth scan. Off until `ci/sundial.yml` says `enabled: true`; see below |

Supporting modules: `ci.versions` (which suite goes with which Chromium),
`ci.suite` (fetches that suite and builds its interpreter), `ci.targets` (the
build targets and why each disabled one is off), `ci.release` (release planning),
`ci.summarize`.

## Running it locally

```bash
python3 -m venv .ci-work/venv && . .ci-work/venv/bin/activate
pip install -r ci/requirements.txt && pip install ./python

python -m pytest ci/tests                 # the pipeline's own tests
python -m ci.run_native --subset rules
python -m ci.run_patch_guards
python -m ci.run_pythonlib
python -m ci.run_typescript               # Node >= 22.15 on PATH

export CAMOUWOLF_BINARY=/path/to/camouwolf
python -m ci.run_install --binary "$CAMOUWOLF_BINARY"
python -m ci.run_build_tester
python -m ci.run_native --subset browser
python -m ci.run_playwright --only tests/async/test_click.py   # or the whole suite, ~10 min
python -m ci.run_skiplist_audit
```

`ci.run_playwright` fetches the suite into `.ci-work/upstream-suite/` and makes
a venv for it from the suite's own pins. `--python` (or `$CI_SUITE_PYTHON`)
reuses an interpreter that already has Playwright and the suite's test
dependencies.

## Which Playwright suite

`ci.versions` picks the newest released playwright-python whose bundled
Chromium is not newer than `browser/upstream.sh`, capped by the launcher's own
`playwright` ceiling in `python/pyproject.toml`. The suite therefore never tests
an API the browser cannot have, and usually trails it by a few milestones; the
summary states both versions.

## The skiplist

`ci/skiplist.yml` lists upstream tests that are expected to fail, each with the
reason. An entry is a decision that the behaviour is intended (an identity held
by the browser, not emulated, for example), never a way to hide a failure. `ci.run_skiplist_audit`
keeps the list honest: once an entry passes it must be removed.

No test is retried: a test that fails once is a failure. Each launch's
identity is seeded from the test that makes it (`seed_draw` in
`ci/pw_camouwolf_plugin.py`, logged as "identity drawn from seed"), so a test
draws the same identity on every run.

Tests of Camouwolf's own behaviour go in `tests/playwright/` as `test_*.py`;
`ci.run_playwright` runs that directory with the same plugin and fixtures.

## The stealth scan

`ci.run_sundial` drives a private detection suite and brings back a grade and
counts; nothing that identifies a vector leaves the module. It is disabled in
`ci/sundial.yml`, with the reasons: the service has no Camouwolf credential,
and its category lists were decided for another engine. `--local-url` scans a
local copy of the suite instead, records the gate `sundial_local`, and refuses
to run in GitHub Actions.

## Targets

A target is an OS (`linux`, `windows`, `macos`) and a CPU (`x86_64`, `arm64`).
`python -m ci.targets` prints them. Only `linux-x86_64` is enabled; the others
are defined, each with the reason it is off, every build stage refuses a
disabled target by quoting that reason, and `release.yml`'s build matrix
excludes exactly those (`ci/tests` checks the two agree). Every target is
cross-compiled on Linux: [`docs/CROSS-COMPILING.md`](../docs/CROSS-COMPILING.md)
says what each needs and how far each has been proven.

## Workflows

`tests.yml` runs on pull requests, forks included, and on pushes to `dev` and
`main`. Every job runs on a GitHub-hosted runner, which is free for a public
repository; there is no self-hosted runner (`ci/tribal-rules.yml`:
`hosted-runners-only`). The browser is built by `build-browser.yml`, the same
workflow a release uses: a hosted job stops at six hours, so the build is a
chain of up to 16 jobs that hand the out/ directory on, then a `package` job,
and a cold build takes about a day. A finished out/ is kept in the Actions
cache, and the next run starts from it, recompiling only the patched files that
changed (`ci/out_cache.py`); pull requests read the cache but never write it. A new push to a pull request cancels the
run it replaces. The stealth check runs only where its credential is set, so
never for a fork. Branch protection requires one check, **All tests passed**
(`ci/branch-protection.json`).

`release.yml` is started by hand, and follows the shape of camoufox's. It calls
`tests.yml` on the same commit, and publishes the Linux x86_64 archive that
run built and tested. Once the tests pass, `build-browser.yml` builds every
other enabled target from that commit (`python -m ci.targets --release-matrix`).
The Windows and macOS toolchains are
packed inside the run on a hosted Windows or macOS runner, encrypted with the
repository secret `CAMOUWOLF_TOOLCHAIN_KEY` (any long random string; set it
before the first release that builds either), and passed on as artifacts that
expire after a day; they are licensed software and are never published. One
GitHub release then carries every archive, and both launchers go out under one
version from `ci.release lib-plan`: `X.Y.Z.devN` / `X.Y.Z-dev.N` for a
prerelease, `X.Y.Z` for a stable release.

Both registries use trusted publishing; no token is stored. Before the first
release, register the publisher once on each:

- PyPI: a pending publisher for project `camouwolf`, owner `CamouBrowser`,
  repository `camouwolf`, workflow `release.yml`, no environment.
- npm: a trusted publisher for package `camouwolf`, same repository and
  workflow. npm needs the package to exist first, so its first version is
  published by a maintainer by hand.

The launchers take camoucore from a git tag of CamouBrowser/camoucore. PyPI
refuses a package with a direct URL dependency, so before the first release
camoucore has to be on PyPI and npm and both launchers have to depend on that
release instead.

Not carried over from the pipeline this one is modelled on: a scheduled release
train, promotion of a prerelease to stable without a rebuild, and publishing
only the package that changed.
