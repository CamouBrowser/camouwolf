# AGENTS.md

Instructions for any coding agent working in this repository or writing code
against Camouwolf. `CLAUDE.md` only imports this file, so there is one set of
rules. Edit them here.

---

## Before you touch anything

1. **Read [`ci/tribal-rules.yml`](ci/tribal-rules.yml).** It lists the decisions
   this repo has already made, each with its reason. They are checked by
   `tests/native/test_tribal_rules.py`. Do not reopen one without new evidence.
   When a new decision is argued and settled, add it there.
2. **Work on a branch off `dev`.** `main` is what releases are cut from.
3. **Nothing private goes in a file, commit message or pull request**: no
   credentials, no machine paths, no names of other private projects, no
   per-vector detail from the stealth scan.

## Before you write a function

Search for an existing implementation first: `python/src/camouwolf/`,
`typescript/src/`, `ci/`, `browser/scripts/`, `scripts/` and
`browser/additions/`. If something close exists, extend it. Duplicated logic is
a serious defect here: the copies drift, and a fingerprint built from two
drifting copies is detectable.

## Code

- **Less code.** Make minimal, general changes, and delete dead code when you
  find it. No scratch files, saved test output, commented-out code or `TODO`
  notes to self.
- **No band-aids.** Fix the main flow. A special case at the call site is a bug
  relocated, not fixed.
- **Fallbacks are a last resort.** They turn a loud failure into a silent wrong
  answer, and in an anti-detect browser a silent wrong answer is a fingerprint.
- **Check real data before inventing a value.** Every spoofed value must be
  something a real device reports. Take it from the recorded distributions
  (fpgen, `python/src/camouwolf/`), not from memory.
- **Every config key has three ends**: a patch that reads it, an entry in
  `browser/settings/properties.json`, and a launcher that writes it. A key
  missing one end fails silently; `python3 scripts/check-config-keys.py` checks
  all three.
- **Comments say why, in a sentence or two.** A longer explanation is a
  decision: put it in `ci/tribal-rules.yml` or `docs/`.
- **Documentation ships with the change** that made it true.

## Tests

- **Run the failing test, not the whole suite.** CI runs the full pipeline.
- **Every bug gets a regression test**: reproduce it with a test and see the
  test fail, fix the bug, see it pass, land both.
- **No flakes.** An intermittent failure means something is non-deterministic.
  Fix that. Never retry, loosen a threshold or skip the test to get green.
- **A missing prerequisite fails; it never passes as a skip.**
- **`ci/skiplist.yml` is for intended differences from stock Chromium**, each
  with its reason. It is not a place to park a failure.
- **Never open a browser window on a desktop.** Launch through the `ci.run_*`
  stages, which own an Xvfb; a hand-rolled launch passes
  `--ozone-platform=x11` and a private `DISPLAY`.

## Security

- **Never commit a credential.** CI secrets live in GitHub Actions secrets, and
  publishing uses OIDC trusted publishing, not tokens.
- **Least privilege** for workflows: `permissions:` per job, and only what that
  job needs.
- **The self-hosted runner executes the commit under test.** Nothing may route
  a fork's pull request to it (`tests.yml`, job `resolve`).

---

# Repository

## What this is

Camouwolf is an anti-detect Chromium for web scraping and automation. This repo
is **not the Chromium source**. It is a patch series and a build system:
`browser/scripts/build.sh` syncs Chromium at the tag pinned in
`browser/upstream.sh`, applies `browser/patches/` in the order `series` lists,
copies in `browser/additions/`, and compiles. The identity a page sees is
answered from C++, from a config the launcher passes in `CAMOU_CONFIG_n`
environment variables, so there is no injected JavaScript for a page to find.

| Path | What it is |
|---|---|
| `browser/upstream.sh` | The pin: Chromium `version`, Camouwolf `release`, `milestone`. |
| `browser/patches/` | The patch series, one behaviour per patch; `series` is the order. [`docs/PATCHES.md`](docs/PATCHES.md) describes each. |
| `browser/additions/camouwolfcfg/` | The C++ config layer the patches read. |
| `browser/settings/properties.json` | Every config key and its type. The launchers carry byte-identical copies. |
| `browser/bundle/` | Per-OS fontconfig and fonts shipped beside the binary. |
| `browser/scripts/` | `build.sh`, `fetch-sources.sh` (a mirror of only the files the series touches), `mkpatch.py`, `apply-patches.sh`, `generate-branding.py`. |
| `scripts/` | Patch checkers (`check-*.py`), `build-camouwolf-incremental.sh`, `release-camouwolf.sh` (packaging). |
| `python/` | The `camouwolf` PyPI package, the reference launcher: draws an identity with fpgen, checks it for coherence, launches the binary through Playwright. |
| `typescript/` | The `camouwolf` npm package, a port of the Python launcher. |
| `tests/` | `build-tester/` (what a page reads against what was claimed), `native/` (settled decisions, leaks, crash recovery), `playwright/` (Camouwolf's own Playwright tests). |
| `ci/` | The test pipeline; [`ci/README.md`](ci/README.md). |
| `docs/` | Patch notes and measurement records. |

## Building

Linux x86_64 only. A cold build needs about 100 GB of disk and several hours.

```bash
bash browser/scripts/build.sh               # sync, patch, compile (CAMOUWOLF_STAGES picks stages)
bash scripts/build-camouwolf-incremental.sh # warm tree: re-apply the series, rebuild what changed
bash scripts/release-camouwolf.sh --target linux --no-release   # package into browser/dist/
```

`CAMOUWOLF_BUILD_DIR` is where the Chromium checkout lives. The header of
`browser/scripts/build.sh` lists the resource and network caps; use them on a
shared machine.

## Changing a patch

Never hand-edit a `.patch` file. Author against the pinned tag:

```bash
bash browser/scripts/fetch-sources.sh               # mirror the pinned tag's copy of the touched files
cp -r browser/.pristine/<version> browser/.work
$EDITOR browser/.work/<file>
python3 browser/scripts/mkpatch.py 0002-navigator-identity <file> [<file> ...]
python3 -m ci.run_patch_guards                      # the series still applies, keys still line up
```

## Testing

`ci/` is the whole pipeline and runs the same way locally and in GitHub
Actions. Branch protection requires one check, **`All tests passed`**.

| You changed | Run |
|---|---|
| A patch, `browser/additions/`, `properties.json` | `python3 -m ci.run_patch_guards`, then against a build: `python3 -m ci.run_build_tester --binary <camouwolf>` |
| Automation behaviour | `python3 -m ci.run_playwright --binary <camouwolf> -k <test>` |
| `python/` | `python3 -m ci.run_pythonlib` |
| `typescript/` | `python3 -m ci.run_typescript` (Node 22.15 or newer) |
| `ci/` itself, a workflow | `python3 -m pytest ci/tests -q` and `python3 -m ci.run_native --subset rules` |

The stealth scan (`ci/run_sundial.py`) reports a grade and counts, never the
individual vectors.

---

# Using Camouwolf

[`python/README.md`](python/README.md) is the reference for every launch
option; the TypeScript launcher takes the same options in camelCase.

- **Do not set identity values by hand.** The identity is drawn as a whole; a
  Playwright `user_agent`, or a `navigator.*` key in `config`, leaves the rest
  describing a different machine. Use `os`, `screen`, `window`, `locale` and
  `geoip`.
- **One identity per browser.** A context-level user agent or device descriptor
  does not override it.
- **Use `geoip=True` with a proxy**, or the timezone and locale are the
  identity's and not the exit's.
- **`os` values are lowercase** `windows`, `macos`, `linux`.
- **Version ceilings:** Playwright below 1.63; Python 3.11 or newer; Node.js
  22.15 or newer.
