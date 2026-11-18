"""Every decision in ci/tribal-rules.yml is still in force. No browser."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES: List[Dict[str, Any]] = yaml.safe_load((REPO_ROOT / "ci" / "tribal-rules.yml").read_text())["rules"]


def files(spec: Dict[str, Any]) -> List[Path]:
    excluded = {p for glob in spec.get("except", []) for p in REPO_ROOT.glob(glob)}
    found = sorted({p for glob in spec["paths"] for p in REPO_ROOT.glob(glob) if p.is_file()} - excluded)
    return [p for p in found if "__pycache__" not in p.parts]


def test_rules_are_well_formed() -> None:
    ids = [rule.get("id") for rule in RULES]
    assert len(ids) == len(set(ids)), "duplicate rule ids"
    for rule in RULES:
        assert str(rule.get("decision", "")).strip() and str(rule.get("why", "")).strip(), rule.get("id")
        kinds = [k for k in ("forbidden", "required", "identical") if k in rule]
        assert len(kinds) == 1, f"{rule['id']}: exactly one check, found {kinds}"


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule["id"])
def test_rule(rule: Dict[str, Any]) -> None:
    if "identical" in rule:
        paths = [REPO_ROOT / name for name in rule["identical"]["files"]]
        contents = {p.read_bytes() for p in paths}
        assert len(contents) == 1, f"{rule['decision']} {rule['why']}"
        return
    kind = "forbidden" if "forbidden" in rule else "required"
    spec = rule[kind]
    pattern = re.compile(spec["pattern"], re.M)
    matched = files(spec)
    assert matched, f"{rule['id']}: {spec['paths']} matches no file, so the rule checks nothing"
    problems = []
    for path in matched:
        text = path.read_text(encoding="utf-8", errors="replace")
        hit = pattern.search(text)
        if kind == "forbidden" and hit:
            line = text.count("\n", 0, hit.start()) + 1
            problems.append(f"{path.relative_to(REPO_ROOT)}:{line}: {hit.group(0)!r}")
        elif kind == "required" and not hit:
            problems.append(f"{path.relative_to(REPO_ROOT)}: no match for {spec['pattern']!r}")
    assert not problems, f"{rule['decision']} {' '.join(str(rule['why']).split())}\n" + "\n".join(problems)
