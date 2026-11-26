"""Self-tests for the pipeline: the parts that decide what a run means.

No browser. The two suite-version tests read public release metadata from
GitHub; everything else is offline. Run with `python -m pytest ci/tests`.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import re
import subprocess
import types
import zipfile
from pathlib import Path

import pytest
import yaml

from ci import _pytest as pytest_helpers
from ci import release, results, run_build, run_build_tester, run_install, run_playwright, summarize, targets, versions
from ci._util import BINARY_ENV, REPO_ROOT, binary_from, read_upstream_sh
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


def test_a_test_draws_the_same_identity_on_every_run(monkeypatch) -> None:
    # Each launch drew a fresh identity, so a test whose outcome depends on the
    # drawn devicePixelRatio (wheel deltas, screenshots) passed or failed by luck.
    import random

    from ci import pw_camouwolf_plugin as plugin

    def run(test: str, launches: int) -> list:
        monkeypatch.setattr(plugin, "_LAUNCHES", {})
        monkeypatch.setenv("PYTEST_CURRENT_TEST", f"{test} (setup)")
        draws = []
        for _ in range(launches):
            plugin.seed_draw()
            draws.append(random.random())
        return draws

    first = run("tests/async/test_input.py::test_wheel_should_work[chromium]", 2)
    assert first == run("tests/async/test_input.py::test_wheel_should_work[chromium]", 2)
    assert first[0] != first[1], "a test's second launch draws its own identity"
    assert first[0] != run("tests/async/test_input.py::test_other[chromium]", 1)[0]


def test_remote_servers_launch_the_build_through_the_launcher(monkeypatch, tmp_path) -> None:
    # Upstream's launch_server fixture hands cli.js a JSON config in a child
    # process, so the launch wrapper never sees it; unwrapped, it launched a
    # stock Chromium that was not installed and printed no endpoint.
    import types

    from ci import browser, pw_camouwolf_plugin

    options = {"executable_path": "/b/camouwolf", "args": ["--a"], "env": {"CAMOU_CONFIG_1": "x"}, "headless": True}
    monkeypatch.setattr(browser, "launcher_options", lambda *a, **k: options)
    seen = []

    class RemoteServer:
        def __init__(self, browser_name, launch_server_options, tmpfile):
            seen.append((browser_name, launch_server_options))

    conftest = types.SimpleNamespace(RemoteServer=RemoteServer)
    assert pw_camouwolf_plugin.install_remote_server(Path("/b/camouwolf"), "linux", [conftest])
    RemoteServer("chromium", {"headless": True, "args": ["--t"]}, tmp_path / "s.json")
    RemoteServer("firefox", {"headless": True}, tmp_path / "f.json")
    assert seen[0][1]["executablePath"] == "/b/camouwolf"
    assert seen[0][1]["args"] == ["--a", "--t"]
    assert seen[0][1]["env"] == {"CAMOU_CONFIG_1": "x"}
    assert seen[1][1] == {"headless": True}
    assert not pw_camouwolf_plugin.install_remote_server(Path("/b/camouwolf"), "linux", [types.SimpleNamespace()])


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


def test_a_failure_is_never_upgraded_within_a_gate() -> None:
    gate = results.GateResult(gate="g")
    gate.record("t", results.FAIL)
    gate.record("t", results.PASS)
    gate.record("u", results.PASS)
    gate.record("u", results.ERROR)
    assert gate.tests == {"t": results.FAIL, "u": results.ERROR}


def test_summary_fails_on_a_missing_required_suite(tmp_path: Path) -> None:
    results.GateResult(gate="pythonlib").finish(results.PASS).save(tmp_path)
    code = summarize.main(["--results-dir", str(tmp_path), "--require", "pythonlib", "playwright",
                           "--markdown", str(tmp_path / "s.md"), "--out", str(tmp_path / "s.json")])
    assert code == 1
    assert "playwright" in (tmp_path / "s.md").read_text()
    assert summarize.main(["--results-dir", str(tmp_path), "--require", "pythonlib",
                           "--markdown", str(tmp_path / "s.md"), "--out", str(tmp_path / "s.json")]) == 0


def test_summary_merges_shards(tmp_path: Path) -> None:
    for index, outcome in ((1, results.PASS), (2, results.FAIL)):
        gate = results.GateResult(gate=f"playwright-{index}of2")
        gate.record(f"t{index}", outcome)
        gate.metrics.update(tag="v1")
        gate.finish(outcome).save(tmp_path)
    merged = summarize.merge_shards(results.load_all(tmp_path))
    assert set(merged) == {"playwright"}
    assert merged["playwright"]["tests"] == {"t1": results.PASS, "t2": results.FAIL}
    assert merged["playwright"]["metrics"]["shards"] == 2 and merged["playwright"]["metrics"]["tag"] == "v1"
    assert merged["playwright"]["status"] == results.FAIL


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


def test_build_tester_imports_the_launcher_from_the_tree() -> None:
    # CI's suite Python does not have the launcher installed, so a plain
    # `import camouwolf` in the driver fails there and nowhere else.
    import ast  # noqa: PLC0415

    tree = ast.parse((REPO_ROOT / "tests" / "build-tester" / "run_tests.py").read_text())
    named = [node.lineno for node in ast.walk(tree)
             if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "camouwolf"
             or isinstance(node, ast.Import) and any(a.name.split(".")[0] == "camouwolf" for a in node.names)]
    assert not named, f"run_tests.py imports camouwolf directly at lines {named}; use ci.browser.import_launcher()"


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


def build_tester_module():
    spec = importlib.util.spec_from_file_location("bt", REPO_ROOT / "tests" / "build-tester" / "run_tests.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("languages, header", [
    # Measured on Chromium 158: navigator.languages, then the Accept-Language tags.
    (["ja-JP", "en-US", "en"], ["ja-JP", "ja", "en-US", "en"]),
    (["fr-FR"], ["fr-FR", "fr"]),
    (["de-DE", "de", "en-US", "en"], ["de-DE", "de", "en-US", "en"]),
])
def test_build_tester_expects_chromes_header_expansion(languages, header) -> None:
    assert build_tester_module().header_languages(languages) == header


@pytest.mark.parametrize("passed, served", [
    ("de-DE,de", ["de-DE", "de", "en-US", "en"]),
    ("ja-JP,ja", ["ja-JP", "en-US", "en"]),
    ("fr-FR", ["fr-FR"]),
])
def test_build_tester_expects_the_off_the_record_language_list(passed, served) -> None:
    options = {"args": ["--lang=x", f"--accept-lang={passed}"]}
    assert build_tester_module().served_languages(options) == served


def test_build_tester_flatten() -> None:
    report = {"profiles": [{"name": "linux-0", "results": {"A": {"x": {"passed": True, "detail": ""}, "y": {"passed": False, "detail": "d"}}}}]}
    assert run_build_tester.flatten(report) == [("linux-0", "A", "x", True, ""), ("linux-0", "A", "y", False, "d")]


# ------------------------------------------------------------------ packaging


def test_every_target_the_launchers_fetch_is_built_as_camoufox_releases() -> None:
    import ast

    # Read from the source: ci's tests run without the launcher installed.
    pkgman = ast.parse((REPO_ROOT / "python" / "src" / "camouwolf" / "pkgman.py").read_text())
    node = next(n for n in ast.walk(pkgman) if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "OS_ARCH_MATRIX")
    OS_ARCH_MATRIX = ast.literal_eval(node.value)
    # camoufox releases every launcher arch but Linux i686, which Chromium dropped.
    released = {f"{targets.OS_SHORT[t.os]}.{t.arch}" for t in targets.TARGETS.values() if t.enabled}
    fetched = {f"{os_short}.{arch}" for os_short, arches in OS_ARCH_MATRIX.items() for arch in arches}
    assert released == fetched - {"lin.i686"}
    for target in targets.TARGETS.values():
        assert target.enabled or (target.disabled_reason and "\n" not in target.disabled_reason)
    with pytest.raises(SystemExit):
        targets.get("linux", "i686")
    with pytest.raises(SystemExit):
        targets.require_enabled("windows", "arm64")


def test_release_builds_every_enabled_target_but_the_tested_one() -> None:
    import yaml

    # tests.yml builds TESTED in the release run; a second build of it would
    # upload a second artifact of the same name.
    tests = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text())
    built = tests["jobs"]["build"]["with"]
    assert f"{built['target']}-{built['arch']}" == targets.TESTED
    assert targets.TARGETS[targets.TESTED].enabled
    released = {f"{m['target']}-{m['arch']}" for m in targets.release_matrix()}
    assert released == {t.name for t in targets.TARGETS.values() if t.enabled} - {targets.TESTED}
    release = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text())
    assert "ci.targets --release-matrix" in json.dumps(release["jobs"]["plan"])
    assert release["jobs"]["build-browser"]["strategy"]["matrix"] == {"include": "${{ fromJSON(needs.plan.outputs.matrix) }}"}


def test_archive_name_matches_the_packaging_script() -> None:
    script = (REPO_ROOT / "scripts" / "release-camouwolf.sh").read_text()
    assert 'ARCHIVE="${PREFIX}-${OS_SHORT}.${ARCH}.zip"' in script
    for os_name, short in targets.OS_SHORT.items():
        assert f"{os_name}) " in script and f"OS_SHORT={short};" in script
    up = read_upstream_sh()
    assert targets.archive_name(targets.TARGETS["linux-x86_64"]) == f"camouwolf-{up['version']}-{up['release']}-lin.x86_64.zip"
    assert targets.archive_name(targets.TARGETS["macos-arm64"]).endswith("-mac.arm64.zip")


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


def _step(job: str, name: str) -> dict:
    tests = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text())
    return next(step for step in tests["jobs"][job]["steps"] if step.get("name") == name)


def test_the_summary_refuses_two_copies_of_one_result(tmp_path: Path) -> None:
    # A self-hosted job once uploaded a shard's result left over from an earlier
    # run beside its own, and the summary judged whichever copy it found first.
    script = _step("summary", "Collect every result file")["run"]
    for artifact, tally in (("results-build", "stale"), ("results-playwright-1of4", "fresh")):
        (tmp_path / "downloaded" / artifact).mkdir(parents=True)
        (tmp_path / "downloaded" / artifact / "playwright-1of4.json").write_text(json.dumps({"tally": tally}))
    proc = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert proc.returncode != 0, proc.stdout
    assert "playwright-1of4.json" in proc.stderr


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


def _patch_apis():
    spec = importlib.util.spec_from_file_location("patch_apis", REPO_ROOT / "scripts" / "check-patch-apis.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("code", [429, 503])
def test_a_throttled_include_lookup_is_never_read_as_missing(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    import urllib.error

    apis = _patch_apis()

    def throttled(url, timeout):
        raise urllib.error.HTTPError(url, code, "busy", {}, None)

    monkeypatch.setattr(apis.urllib.request, "urlopen", throttled)
    monkeypatch.setattr(apis.time, "sleep", lambda _: None)
    cache: dict = {}
    with pytest.raises(apis.Unverifiable):
        apis.exists_upstream("base/values.h", "1.0", cache, offline=False)
    assert "base/values.h" not in cache


def test_a_timed_out_include_lookup_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read timeout surfaces as a bare TimeoutError, not a URLError, and failed a guard run."""
    import base64
    import io

    apis = _patch_apis()
    replies = [TimeoutError("The read operation timed out"), base64.b64encode(b"int x;\n")]

    def slow_then_served(url, timeout):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(reply)

    monkeypatch.setattr(apis.urllib.request, "urlopen", slow_then_served)
    monkeypatch.setattr(apis.time, "sleep", lambda _: None)
    assert apis.googlesource("base/values.h", "1.0") == "int x;\n"


def test_only_a_404_means_an_include_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    apis = _patch_apis()

    def missing(url, timeout):
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    monkeypatch.setattr(apis.urllib.request, "urlopen", missing)
    assert apis.exists_upstream("base/nope.h", "1.0", {}, offline=False) is False


def test_a_generated_mojom_header_is_checked_by_its_mojom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`foo.mojom.h` exists in no checkout; the build makes it from `foo.mojom`."""
    apis = _patch_apis()
    patches = tmp_path / "patches"
    patches.mkdir()
    (patches / "0001-x.patch").write_text(
        "+++ b/a.cc\n@@ -1,0 +1,2 @@\n"
        '+#include "services/network/public/mojom/network_context.mojom.h"\n'
        '+#include "services/network/public/mojom/nope.mojom.h"\n'
    )
    src = tmp_path / "src"
    (src / "services/network/public/mojom").mkdir(parents=True)
    (src / "services/network/public/mojom/network_context.mojom").write_text("")
    monkeypatch.setattr(apis, "PATCHES", patches)
    monkeypatch.setattr(apis, "CACHE", tmp_path / "cache.json")
    problems, checked, _ = apis.check_includes("1.0", offline=True, src=src)
    assert checked == 1
    assert problems == ['0001-x.patch: #include "services/network/public/mojom/nope.mojom.h" not found in ' + str(src)]


# ---------------------------------------------------------------- fetch-sources


def test_fetch_sources_retries_a_throttled_file_and_reads_only_404_as_missing(tmp_path: Path) -> None:
    """A 429 from googlesource is the shared runner being throttled, not a moved file."""
    import base64
    import http.server
    import subprocess
    import threading

    throttled = {"once": True}

    class Gitiles(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/busy.cc") and throttled.pop("once", False):
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                return
            if self.path.startswith("/gone.cc"):
                self.send_error(404)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(base64.b64encode(b"int x;\n"))

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gitiles)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def fetch(*paths: str) -> subprocess.CompletedProcess:
            files = tmp_path / "files.txt"
            files.write_text("\n".join(paths) + "\n")
            env = {"PATH": "/usr/bin:/bin", "CAMOUWOLF_FILELIST": str(files),
                   "CAMOUWOLF_SOURCE_BASE": f"http://127.0.0.1:{server.server_port}"}
            return subprocess.run(["bash", str(REPO_ROOT / "browser" / "scripts" / "fetch-sources.sh"), str(tmp_path / "m")],
                                  env=env, capture_output=True, text=True, timeout=120)

        ok = fetch("busy.cc")
        assert ok.returncode == 0, ok.stderr
        assert (tmp_path / "m" / "busy.cc").read_text() == "int x;\n"
        gone = fetch("gone.cc")
        assert gone.returncode == 1 and "MISSING gone.cc" in gone.stderr, gone.stderr
        assert not (tmp_path / "m" / "gone.cc").exists()
    finally:
        server.shutdown()


def test_packaging_fetches_the_font_bundle_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A package without the bundle fails every foreign-OS font check.
    calls: list = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd[1])
        return types.SimpleNamespace(ok=cmd[1] != str(run_build.FETCH_FONTS), code=1)

    monkeypatch.setattr(run_build, "run", fake_run)
    assert run_build.main(["--results-dir", str(tmp_path)]) == 1
    assert calls[-1] == str(run_build.FETCH_FONTS)  # a failed fetch stops before packaging


def test_the_suite_interpreter_has_what_tests_native_imports() -> None:
    # run_native runs tests/native with the suite's interpreter, which once lacked psutil.
    import ast
    import sys

    from ci import suite

    native = REPO_ROOT / "tests" / "native"
    local = {p.stem for p in native.glob("*.py")} | {"ci", "playwright"}
    imported = set()
    for path in native.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    third_party = imported - set(sys.stdlib_module_names) - local
    assert third_party <= set(suite._REQUIRED_MODULES), third_party - set(suite._REQUIRED_MODULES)


def test_dead_patch_check_applies_the_series_in_order(tmp_path: Path) -> None:
    # Two patches that each apply to pristine can still collide when the build
    # applies them one after the other.
    spec = importlib.util.spec_from_file_location("dead", REPO_ROOT / "scripts" / "check-dead-patches.py")
    assert spec is not None and spec.loader is not None
    dead = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dead)
    src, patches = tmp_path / "src", tmp_path / "patches"
    src.mkdir()
    patches.mkdir()
    lines = [f"line {i}\n" for i in range(20)]
    (src / "f.cc").write_text("".join(lines))
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=k@k", "-c", "user.name=k", "commit", "-qm", "base"]):
        subprocess.run(["git", *cmd], cwd=src, check=True)

    def patch(name: str, at: int, text: str) -> str:
        new = [*lines[:at], text, *lines[at:]]
        diff = "".join(difflib.unified_diff(lines, new, "a/f.cc", "b/f.cc"))
        (patches / name).write_text(f"diff --git a/f.cc b/f.cc\n{diff}")
        return name

    names = [patch("1.patch", 3, "one\n"), patch("2.patch", 4, "two\n"), patch("3.patch", 15, "three\n")]
    assert [v for _, v, _ in dead.check_series(src, names, patches)] == ["OK", "DEAD", "OK"]
