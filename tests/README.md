# tests/

Everything that verifies Camouwolf — the launcher in `src/` and the Chromium fork in
`browser/`.

## Layout

| Path | What it is |
| --- | --- |
| `launcher/` | The launcher's own tests: vitest (`*.test.ts`) plus `run-tests.sh`, which also drives the upstream microsoft/playwright suite against a built browser. No browser needed for the vitest half. |
| `compare/` | Browser-driving probes. These need a built binary and take minutes. |
| `lib/` | The shared harness every probe uses (`drive.mjs`). |
| `ci/` | `run-heavy-suite.sh` — the enforced gate for PRs into `main`. |

## The two halves, and why both

**Stealth** and **functionality** fail independently, and a suite that only
checks one is worse than useless because it reports green.

- A patch can spoof a surface perfectly and break ordinary web behavior. The
  upstream Playwright suite catches that.
- A patch can leave every Playwright test passing and spoof nothing at all —
  most often because the launcher writes a config key that no patch reads. That
  failure is completely silent: the browser starts, the launch succeeds, the
  surface is stock. `compare/stealth-surface.mjs` catches it by comparing what
  the browser *reports* against what the launcher *claimed*, key by key.

## Running the gate

```bash
bash tests/ci/run-heavy-suite.sh
```

Must reach `HEAVY SUITE: PASS`. Useful env: `CI_STAGES="patches surface cdp"` to
run a subset, `CI_SKIP_BUILD=1` to judge an existing build (**local only** — CI
never sets it, so the gate always judges a binary it built from the tree).

It builds first, always. A patch once got "validated" against a stale binary and
reported a false pass; the build step is why that cannot recur.

## The oracle is sundial

`daijro/sundial`, run locally. We do not reinvent fingerprint checks: sundial
bundles CreepJS, fpworker, reversed Browserscan and Pixelscan, Browserleaks,
Scrapfly's tools, the rebrowser detector, and its own cross-signal consistency
tests — roughly 760 checks maintained by people whose job is finding these.
Anything we wrote ourselves would be a weaker copy that goes stale.

Sundial's own `osConsistent`, rasterizer and trig detectors are the blocking
signals; the rest of its failures are reported but non-blocking, because it
includes checks that flag legitimate configurations and treating the raw count
as a gate would make the suite un-greenable and therefore ignored.

## How the scripts find the browser

`lib/drive.mjs` resolves the binary from `CAMOUWOLF_EXECUTABLE_PATH`, then
`browser-cache/camouwolf`. It deliberately does **not** fall back to a production
install: a probe that silently tests last week's release instead of the build
you just made reports green for code that was never run.

## Layers

Camouwolf's patch series covers **layer C** — the fingerprint surface. The suite
also covers what the patches cannot:

- **Layer A** (driver artifacts: chromedriver vars, `cdc_` globals)
- **Layer B** (CDP side effects: the `Runtime.enable` execution-context leak,
  init-script residue)

Both are launcher/driver concerns, verified by `compare/cdp-leak.mjs`. No
browser patch can fix a driver that announces itself, so they are checked rather
than assumed.
