# Upstream pin for the Camouwolf browser build.
#
# `version` is a Chromium release tag (`git checkout tags/$version` in a
# chromium/src checkout). `release` is Camouwolf's own build number on top of it.
#
# Bumping `version`:
#   1. edit it here
#   2. `python3 scripts/check-dead-patches.py`  — every hunk is tested fwd+reverse
#      against the pristine tree; reconcile DEAD/CORRUPT/MISSFILE before building
#   3. `bash scripts/release-camouwolf.sh --target linux`
#
# Latest stable at time of pin (2026-11-18): Chromium 158.0.8127.36 on Linux
# beta; Linux stable was still on 151.0.7922.137). Dev was 153.0.8003.0.
version=158.0.8127.36
release=alpha.1

# Milestone, derived — used for the UA major, UA-CH brand list, and the preset
# retarget. Keep in sync with `version`.
milestone=158
