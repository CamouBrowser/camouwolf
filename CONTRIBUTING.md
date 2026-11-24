# Contributing to Camouwolf

| Contribution | How |
|---|---|
| Bug report | Open an issue with steps to reproduce, expected behaviour and actual behaviour. |
| Detection report | Open an issue naming the site or test page and what it reported. |
| Code or docs | Branch off `dev` and open a pull request. |

## Setup

The launchers need no browser build to develop: `pip install -e ./python`, and
`pnpm install` in `typescript/` (Node 22.15 or newer). Building the browser is
described in [`AGENTS.md`](AGENTS.md#building); it needs Linux, about 100 GB of
disk and several hours the first time.

## Pull request rules

The engineering rules in [`AGENTS.md`](AGENTS.md) apply to every change,
whether a person or an agent wrote it.

1. Keep commits focused: one logical change per commit.
2. Describe what you changed and why.
3. The test pipeline must pass.

## Testing

[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs on every pull
request. Branch protection requires one check, **`All tests passed`**.

Every suite runs for every pull request, forks included, on GitHub-hosted
runners. The browser is built from scratch in the run, which takes about a day;
the static checks and launcher suites report within minutes. The stealth check
needs a credential a fork's pull request does not get, so it is skipped there.

While you work, run the suite that covers your change. Each CI job is
`python3 -m ci.<stage>` and runs the same way locally:

| You changed | Run |
|---|---|
| A patch or a config key | `python3 -m ci.run_patch_guards` |
| `python/` | `python3 -m ci.run_pythonlib` |
| `typescript/` | `python3 -m ci.run_typescript` |
| Anything, against a built browser | the browser stages in [`ci/README.md`](ci/README.md#running-it-locally) |

A test that upstream Playwright expects to pass and Camouwolf fails on purpose
goes in [`ci/skiplist.yml`](ci/skiplist.yml) with its reason. A test that fails
some of the time is a bug to fix, not an entry to add.

## Reporting issues

Include the package and browser versions (`camouwolf version`), your OS and
Python or Node.js version, and a minimal example that reproduces the problem.
