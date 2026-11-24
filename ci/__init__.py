"""Camouwolf's test pipeline.

Everything that *runs* a suite and reports what happened lives here, and is
driven identically from a pull request, a push, and a release: there is exactly
one definition of "the tests pass", and a workflow only ever calls
`python -m ci.<stage>`.

  versions.py       which Chromium the tree builds, and which Playwright suite tests it
  suite.py          fetch and prepare that Playwright suite
  skiplist.yml      upstream tests Camouwolf does not pass by design, each with a reason
  pw_camouwolf_plugin.py   launcher redirection, skips, sharding
  browser.py        launch through the launcher, on a private X display
  targets.py        the build targets, and why two of them are off
  run_*.py          one runner per suite; each writes one result file
  release.py        version planning for the browser archive and the two packages
  summarize.py      fold the result files into a verdict and a readable table
"""
