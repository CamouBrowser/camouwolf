"""An arm64 identity makes the NaNs an ARM64 CPU makes, in every tier (0049).

ARM64 (FPCR.DN=0) returns the first signalling NaN operand quieted, else the
first quiet NaN operand, else the default NaN +7ff8...; x86 returns its first
operand and makes -fff8... The model below reproduces ARM64 Chrome's recorded
results; the page computes each case cold, after tier-up, and in wasm.
"""

from __future__ import annotations

import math
import platform
import struct
from pathlib import Path
from typing import Callable, Dict, List

from playwright.sync_api import sync_playwright

from _procs import options

INPUTS = [0x7FF8000000000000, 0xFFF8000000000000, 0x7FF8000000000123, 0xFFF80000000ABCDE,
          0x7FF0000000000001, 0xFFF4000000000000, 0x7FF0000000000000, 0xFFF0000000000000,
          0, 0x8000000000000000, 0x3FF0000000000000, 0xBFF8000000000000]
DEFAULT_NAN = 0x7FF8000000000000
QUIET = 0x0008000000000000

# Each case runs cold, hot (after enough calls for the optimizing tiers) and
# through a wasm f64.add, whose module is built inline.
PROBE = """(inputs) => {
  const f64 = new Float64Array(1), u64 = new BigUint64Array(f64.buffer);
  const raw = new Float64Array(new BigUint64Array(inputs.map(BigInt)).buffer);
  const bits = (x) => { f64[0] = x; return u64[0].toString(16); };
  const ops = {
    add: (a, b) => a + b, sub: (a, b) => a - b, mul: (a, b) => a * b,
    div: (a, b) => a / b, sqrt: (a) => Math.sqrt(a),
  };
  const wasm = new WebAssembly.Instance(new WebAssembly.Module(new Uint8Array([
    0, 97, 115, 109, 1, 0, 0, 0, 1, 7, 1, 96, 2, 124, 124, 1, 124, 3, 2, 1, 0,
    7, 7, 1, 3, 97, 100, 100, 0, 0, 10, 9, 1, 7, 0, 32, 0, 32, 1, 160, 11]))).exports.add;
  ops.wasm = wasm;
  const run = (f) => Array.from(raw, (a) => Array.from(raw, (b) => bits(f(a, b))));
  const out = {};
  for (const [name, f] of Object.entries(ops)) {
    out[name + '.cold'] = run(f);
    for (let i = 0; i < 20000; i++) run(f);
    out[name + '.hot'] = run(f);
  }
  return out;
}"""


def double(b: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", b))[0]


def nan(b: int) -> bool:
    return (b >> 52) & 0x7FF == 0x7FF and b & 0xFFFFFFFFFFFFF != 0


def arm64(op: Callable[[float, float], float], *operands: int) -> int:
    for signalling in (True, False):
        for b in operands:
            if nan(b) and bool(b & QUIET) != signalling:
                return b | QUIET
    result = op(*map(double, operands))
    return DEFAULT_NAN if math.isnan(result) else struct.unpack("<Q", struct.pack("<d", result))[0]


def divide(a: float, b: float) -> float:
    if b == 0:
        return math.nan if a == 0 else math.copysign(math.inf, a) * math.copysign(1, b)
    return a / b


BINARY: Dict[str, Callable[[float, float], float]] = {
    "add": lambda a, b: a + b, "sub": lambda a, b: a - b, "mul": lambda a, b: a * b, "div": divide,
    "wasm": lambda a, b: a + b,
}


def expected(name: str) -> List[List[str]]:
    if name == "sqrt":
        root = lambda a: math.nan if a < 0 else math.sqrt(a)  # noqa: E731
        return [[f"{arm64(root, a):x}"] * len(INPUTS) for a in INPUTS]
    return [[f"{arm64(BINARY[name], a, b):x}" for b in INPUTS] for a in INPUTS]


def probe(binary: Path, **kwargs) -> Dict[str, List[List[str]]]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**options(binary, **kwargs))
        try:
            return browser.new_page().evaluate(PROBE, INPUTS)
        finally:
            browser.close()


def test_an_arm64_identity_makes_arm64s_nans_in_every_tier(binary: Path) -> None:
    seen = probe(binary, os_name="macos", cpu={"arch": "arm"})
    wrong = [case for case, results in seen.items() if results != expected(case.split(".")[0])]
    assert not wrong, f"not ARM64's NaNs: {wrong}"


def test_an_x86_identity_on_x86_makes_the_hosts_nans(binary: Path) -> None:
    assert platform.machine().lower() in ("x86_64", "amd64"), "needs an x86 host"
    seen = probe(binary, os_name="linux", cpu={"arch": "x86"})
    infinities = INPUTS.index(0x7FF0000000000000), INPUTS.index(0xFFF0000000000000)
    for case in ("add.cold", "add.hot", "wasm.cold", "wasm.hot"):
        assert seen[case][infinities[0]][infinities[1]] == "fff8000000000000", case
