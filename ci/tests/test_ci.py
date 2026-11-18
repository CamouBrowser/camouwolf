"""Self-tests for the pipeline: the parts that decide what a run means.

No browser. The two suite-version tests read public release metadata from
GitHub; everything else is offline. Run with `python -m pytest ci/tests`.
"""

from __future__ import annotations

import importlib.util
import json
import re
import zipfile
from pathlib import Path

import pytest
import yaml

from ci import _pytest as pytest_helpers
from ci import release, results, run_build, run_build_tester, run_install, run_playwright, summarize, targets, versions
from ci._util import BINARY_ENV, LEGACY_ENV_PREFIX_ENV, REPO_ROOT, binary_from, read_upstream_sh
from ci.browser import with_legacy_env
from ci.pw_camouwolf_plugin import load_skiplist, merge_launch, parse_shard, shard_of, skip_reason
from ci.run_prepare import is_transient
from ci.run_skiplist_audit import targets as audit_targets

WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))


# ---------------------------------------------------------------- the binary


def test_binary_has_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BINARY_ENV, raising=False)
    with pytest.raises(FileNotFoundError):
        binary_from(None)


def test_binary_from_env_and_argument(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.write_text("")
    two.write_text("")
    monkeypatch.setenv(BINARY_ENV, str(one))
    assert binary_from(None) == one
    assert binary_from(two) == two  # the argument wins
    with pytest.raises(FileNotFoundError):
        binary_from(tmp_path / "missing")


# ------------------------------------------------------------ legacy env


def test_legacy_env_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LEGACY_ENV_PREFIX_ENV, raising=False)
    env = {"CAMOU_CONFIG_1": "a"}
    assert with_legacy_env(dict(env)) == env


def test_legacy_env_duplicates_every_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LEGACY_ENV_PREFIX_ENV, "KRAFT_CONFIG")
    env = with_legacy_env({"CAMOU_CONFIG_1": "a", "CAMOU_CONFIG_2": "b", "CAMOUWOLF_UCRT_MATH": "1", "TZ": "UTC"})
    assert env["KRAFT_CONFIG_1"] == "a" and env["KRAFT_CONFIG_2"] == "b"
    assert env["KRAFT_UCRT_MATH"] == "1"
    # The launcher's own variables are still there: the switch adds, never renames.
    assert env["CAMOU_CONFIG_1"] == "a" and env["TZ"] == "UTC"


# --------------------------------------------------------------- the plugin


def test_merge_launch_layers_the_test_over_the_launcher() -> None:
    options = {"executable_path": "/b/camouwolf", "args": ["--a"], "ignore_default_args": ["--enable-automation"],
               "env": {"CAMOU_CONFIG_1": "x", "TZ": "UTC"}, "headless": True}
    merged = merge_launch({"args": ["--t"], "env": {"TZ": "Asia/Tokyo"}, "ignoreDefaultArgs": ["--mute-audio"]}, options)
    assert merged["executablePath"] == "/b/camouwolf"
    assert merged["args"] == ["--a", "--t"]
    assert merged["ignoreDefaultArgs"] == ["--enable-automation", "--mute-audio"]
    assert merged["env"] == {"CAMOU_CONFIG_1": "x", "TZ": "Asia/Tokyo"}
    assert merged["headless"] is True


def test_merge_launch_keeps_a_tests_own_executable_and_headless() -> None:
    options = {"executable_path": "/b/camouwolf", "headless": True}
    merged = merge_launch({"executablePath": "/other", "headless": False, "ignoreDefaultArgs": True}, options)
    assert merged["executablePath"] == "/other"
    assert merged["headless"] is False
    assert merged["ignoreDefaultArgs"] is True


def test_shards_partition_the_suite() -> None:
    ids = [f"tests/async/test_{i}.py::test_{j}" for i in range(40) for j in range(10)]
    total = 6
    buckets: dict = {}
    for node in ids:
        buckets.setdefault(shard_of(node, total), []).append(node)
    assert len(buckets) == total, "a shard got nothing"
    assert sorted(sum(buckets.values(), [])) == sorted(ids)
    assert all(shard_of(node, total) == shard_of(node, total) for node in ids[:20])
    assert parse_shard("3/6") == (3, 6)
    assert parse_shard(None) is None
    for bad in ("0/6", "7/6", "x", "3"):
        with pytest.raises(RuntimeError):
            parse_shard(bad)


def test_skiplist_entries_are_valid() -> None:
    entries = load_skiplist(REPO_ROOT / "ci" / "skiplist.yml")
    assert entries, "the skiplist is expected to have entries"
    assert summarize.validate_skiplist() == []
    for entry in entries:
        assert sum(k in entry for k in ("test", "module", "pattern")) == 1, entry
        target = entry.get("test") or entry.get("module")
        if target:
            assert target.startswith("tests/"), f"{target}: entries are pytest node ids of the upstream checkout"


def test_skip_reason_matches_parametrized_ids() -> None:
    entries = [{"test": "tests/async/test_a.py::test_x", "reason": "r1"}, {"module": "tests/sync/test_b.py", "reason": "r2"}]
    assert skip_reason("tests/async/test_a.py::test_x[chromium]", entries) == "r1"
    assert skip_reason("tests/async/test_a.py::test_xy[chromium]", entries) is None
    assert skip_reason("tests/sync/test_b.py::test_any", entries) == "r2"
    assert skip_reason("tests/sync/test_c.py::test_any", entries) is None


def test_validate_skiplist_rejects_an_empty_reason(tmp_path: Path) -> None:
    path = tmp_path / "skiplist.yml"
    path.write_text("schema: 1\nskip:\n  - test: tests/a.py::t\n    reason: ''\n")
    assert summarize.validate_skiplist(path)


def test_audit_reports_patterns_it_cannot_run() -> None:
    selectable, unresolved = audit_targets([{"test": "tests/a.py::t", "reason": "r"}, {"pattern": "xyz", "reason": "r"}])
    assert selectable == ["tests/a.py::t"] and unresolved == ["xyz"]


# ------------------------------------------------------------------ versions


def test_suite_never_runs_ahead_of_the_browser() -> None:
    up = read_upstream_sh()
    resolved = versions.resolve(browser_version=up["version"])
    assert versions.parse_version(resolved["playwright_chromium"]) <= versions.parse_version(up["version"])
    older = versions.resolve(browser_version="148.0.7778.96")
    assert older["playwright_tag"] == "v1.60.0"


def test_suite_respects_the_launchers_playwright_ceiling() -> None:
    ceiling = re.search(r'playwright = "<(\d+)\.(\d+)"', (REPO_ROOT / "python" / "pyproject.toml").read_text())
    assert ceiling
    tag = versions.resolve(browser_version="999.0.0.0")["playwright_tag"]
    assert tuple(int(x) for x in tag.lstrip("v").split(".")[:2]) < (int(ceiling.group(1)), int(ceiling.group(2)))


def test_upstream_sh_is_coherent() -> None:
    assert release.problems() == []


# -------------------------------------------------------------------- junit


def test_junit_outcomes(tmp_path: Path) -> None:
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.async.test_a" name="test_ok[chromium]"/>'
        '<testcase classname="tests.async.test_a" name="test_bad"><failure message="x"/></testcase>'
        '<testcase classname="tests.async.test_a" name="test_err"><error message="x"/></testcase>'
        '<testcase classname="tests.async.test_a" name="test_skip"><skipped/></testcase>'
        '</testsuite></testsuites>'
    )
    outcomes = pytest_helpers.parse_junit(junit)
    assert sorted(outcomes.values()) == ["error", "fail", "pass", "skip"]
    assert pytest_helpers.parse_junit(tmp_path / "missing.xml") == {}


# ------------------------------------------------------------------- results


def test_a_pass_is_never_downgraded_within_a_gate() -> None:
    gate = results.GateResult(gate="g")
    gate.record("t", results.PASS)
    gate.record("t", results.FAIL)
    assert gate.tests["t"] == results.PASS


def test_summary_fails_on_a_missing_required_suite(tmp_path: Path) -> None:
    results.GateResult(gate="pythonlib").finish(results.PASS).save(tmp_path)
    code = summarize.main(["--results-dir", str(tmp_path), "--require", "pythonlib", "playwright",
                           "--markdown", str(tmp_path / "s.md"), "--out", str(tmp_path / "s.json")])
    assert code == 1
    assert "playwright" in (tmp_path / "s.md").read_text()
    assert summarize.main(["--results-dir", str(tmp_path), "--require", "pythonlib",
                           "--markdown", str(tmp_path / "s.md"), "--out", str(tmp_path / "s.json")]) == 0


def test_summary_merges_shards_and_sums_flaky(tmp_path: Path) -> None:
    for index, flaky in ((1, ["a"]), (2, ["b", "c"])):
        gate = results.GateResult(gate=f"playwright-{index}of2")
        gate.record(f"t{index}", results.PASS)
        gate.metrics.update(flaky_count=len(flaky), flaky=flaky)
        gate.finish(results.PASS).save(tmp_path)
    merged = summarize.merge_shards(results.load_all(tmp_path))
    assert set(merged) == {"playwright"}
    assert merged["playwright"]["metrics"]["flaky_count"] == 3
    assert sorted(merged["playwright"]["metrics"]["flaky"]) == ["a", "b", "c"]


def test_summary_treats_a_failed_suite_as_a_failure(tmp_path: Path) -> None:
    gate = results.GateResult(gate="build_tester")
    gate.record("x", results.FAIL)
    gate.finish(results.FAIL).save(tmp_path)
    assert summarize.main(["--results-dir", str(tmp_path), "--require", "build_tester",
                           "--markdown", str(tmp_path / "s.md"), "--out", str(tmp_path / "s.json")]) == 1


# ------------------------------------------------------------- the playwright runner


def test_every_upstream_path_is_run_or_excluded(tmp_path: Path) -> None:
    for name in ("async", "sync", "common", "brand_new"):
        (tmp_path / "tests" / name).mkdir(parents=True)
        (tmp_path / "tests" / name / "test_x.py").write_text("")
    (tmp_path / "tests" / "assets").mkdir()
    (tmp_path / "tests" / "test_installation.py").write_text("")
    (tmp_path / "tests" / "test_reference_count_async.py").write_text("")
    assert run_playwright.unclaimed(tmp_path) == ["tests/brand_new/"]


def test_playwright_runs_through_the_plugin_as_full_chromium() -> None:
    args = run_playwright.BASE_ARGS
    assert args[args.index("-p") + 1] == "ci.pw_camouwolf_plugin"
    assert args[args.index("--browser") + 1] == "chromium"
    assert args[args.index("--browser-channel") + 1] == "chromium"
    assert "--timeout" in args, "one hung test must not hold its group"


# ------------------------------------------------------------- build-tester policy


def test_build_tester_policy_names_every_category_once() -> None:
    policy = run_build_tester.load_policy()
    required, reported = set(policy["required_categories"]), set(policy["reported_categories"])
    assert not required & reported
    spec = importlib.util.spec_from_file_location("bt", REPO_ROOT / "tests" / "build-tester" / "run_tests.py")
    source = (REPO_ROOT / "tests" / "build-tester" / "run_tests.py").read_text()
    page = (REPO_ROOT / "tests" / "build-tester" / "page" / "checks.js").read_text()
    emitted = set(re.findall(r'out\["([^"]+)"\] = \{\}', source)) | set(re.findall(r'checks\["([^"]+)"\] = \{\}', page))
    emitted.add("Launch")
    assert spec is not None
    assert emitted == required | reported, (emitted - required - reported, (required | reported) - emitted)


def test_build_tester_flatten() -> None:
    report = {"profiles": [{"name": "linux-0", "results": {"A": {"x": {"passed": True, "detail": ""}, "y": {"passed": False, "detail": "d"}}}}]}
    assert run_build_tester.flatten(report) == [("linux-0", "A", "x", True, ""), ("linux-0", "A", "y", False, "d")]


# ------------------------------------------------------------------ packaging


def test_only_linux_is_enabled_and_the_others_say_why() -> None:
    assert [t.name for t in targets.TARGETS.values() if t.enabled] == ["linux"]
    for target in targets.TARGETS.values():
        assert target.enabled or (target.disabled_reason and "\n" not in target.disabled_reason)
    with pytest.raises(SystemExit):
        targets.require_enabled("windows")


def test_archive_name_matches_the_packaging_script() -> None:
    script = (REPO_ROOT / "scripts" / "release-camouwolf.sh").read_text()
    for target in targets.TARGETS.values():
        assert f"${{PREFIX}}-{target.archive_suffix}.zip" in script
    up = read_upstream_sh()
    assert targets.archive_name(targets.TARGETS["linux"]) == f"camouwolf-{up['version']}-{up['release']}-lin.x86_64.zip"


def test_checksum_must_match(tmp_path: Path) -> None:
    import hashlib

    archive = tmp_path / "a.zip"
    archive.write_bytes(b"payload")
    assert not run_build.checksum_ok(archive)
    (tmp_path / "a.zip.sha256").write_text("0" * 64 + "  a.zip\n")
    assert not run_build.checksum_ok(archive)
    (tmp_path / "a.zip.sha256").write_text(hashlib.sha256(b"payload").hexdigest() + "  a.zip\n")
    assert run_build.checksum_ok(archive)


def test_unpack_refuses_an_escaping_entry_and_a_flat_archive(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../escape", "x")
    with pytest.raises(ValueError):
        run_install.unpack(bad, tmp_path / "out1")
    flat = tmp_path / "flat.zip"
    with zipfile.ZipFile(flat, "w") as archive:
        archive.writestr("camouwolf", "x")
        archive.writestr("other", "x")
    with pytest.raises(ValueError):
        run_install.unpack(flat, tmp_path / "out2")
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr("camouwolf-1/camouwolf", "x")
    assert run_install.unpack(good, tmp_path / "out3") == tmp_path / "out3" / "camouwolf-1" / "camouwolf"


def test_only_network_failures_are_retried() -> None:
    assert is_transient("fatal: unable to access: Connection reset by peer")
    assert is_transient("error: RPC failed; HTTP 502")
    assert not is_transient("error: patch failed: chrome/browser/foo.cc:12")
    assert not is_transient("ninja: build stopped: subcommand failed.")


# -------------------------------------------------------------------- release


def test_prerelease_numbers_follow_both_registries() -> None:
    plan = release.plan_prerelease("0.1.0", ["0.1.0.dev1", "0.1.0.dev2"], ["0.1.0-dev.4", "0.0.9"])
    assert (plan.pypi, plan.npm) == ("0.1.0.dev5", "0.1.0-dev.5")
    assert release.plan_prerelease("0.1.0", [], []).pypi == "0.1.0.dev1"
    with pytest.raises(ValueError):
        release.plan_prerelease("0.1.0", ["0.1.0"], [])


def test_stable_refuses_a_published_version() -> None:
    assert release.plan_stable("0.2.0", ["0.1.0"], ["0.1.0"]).npm == "0.2.0"
    with pytest.raises(ValueError):
        release.plan_stable("0.1.0", [], ["0.1.0"])
    with pytest.raises(ValueError):
        release.plan_stable("0.1", [], [])


def test_stamp_writes_both_packages(tmp_path: Path) -> None:
    (tmp_path / "python").mkdir()
    (tmp_path / "typescript").mkdir()
    (tmp_path / "python" / "pyproject.toml").write_text('[tool.poetry]\nname = "camouwolf"\nversion = "0.1.0"\n')
    (tmp_path / "typescript" / "package.json").write_text('{\n  "name": "camouwolf",\n  "version": "0.1.0"\n}\n')
    release.stamp("0.1.0.dev3", "0.1.0-dev.3", tmp_path)
    assert 'version = "0.1.0.dev3"' in (tmp_path / "python" / "pyproject.toml").read_text()
    assert json.loads((tmp_path / "typescript" / "package.json").read_text())["version"] == "0.1.0-dev.3"


def test_browser_tag() -> None:
    assert release.browser_tag({"version": "158.0.8127.36", "release": "alpha.1"}) == "v158.0.8127.36-alpha.1"


# ------------------------------------------------------------------ workflows


def test_workflows_parse_and_only_call_stages_that_exist() -> None:
    assert WORKFLOWS, "no workflows"
    for path in WORKFLOWS:
        document = yaml.safe_load(path.read_text())
        assert isinstance(document, dict) and document.get("jobs"), path.name
        for module in re.findall(r"python3? -m (ci\.[a-z_]+)", path.read_text()):
            assert importlib.util.find_spec(module) is not None, f"{path.name} calls {module}, which does not exist"


def test_browser_jobs_cannot_run_for_a_fork() -> None:
    """Every job on the self-hosted runner depends on the trust decision."""
    tests = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text())
    self_hosted = {name: job for name, job in tests["jobs"].items() if "self-hosted" in json.dumps(job.get("runs-on", ""))}
    assert self_hosted, "the browser tier is expected to run on a self-hosted runner"
    for name, job in self_hosted.items():
        assert "needs.resolve.outputs.run_browser == 'true'" in str(job.get("if", "")), name
        needs = job.get("needs")
        assert "resolve" in ([needs] if isinstance(needs, str) else needs), name


def test_branch_protection_names_the_gate_job() -> None:
    contexts = json.loads((REPO_ROOT / "ci" / "branch-protection.json").read_text())["required_status_checks"]["contexts"]
    tests = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text())
    names = {job.get("name") for job in tests["jobs"].values()}
    assert set(contexts) <= names


def test_sundial_only_publishes_whitelisted_keys() -> None:
    from ci import run_sundial

    with pytest.raises(Exception):
        run_sundial._assert_publishable({"grade": "A", "vector_name": "x"})
    assert run_sundial.is_enabled({"enabled": True}) and not run_sundial.is_enabled({})


def test_a_timed_out_command_takes_its_children_with_it(tmp_path: Path) -> None:
    import os
    import time

    from ci._util import run

    pidfile = tmp_path / "pid"
    result = run(["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], timeout=1, kill_group=True)
    assert result.code == 124
    child = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError("the grandchild outlived the timeout")
