"""Shared fixtures for Camouwolf's own suite (python -m ci.run_native)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def binary() -> Path:
    from ci._util import binary_from  # noqa: PLC0415

    return binary_from(None)


@pytest.fixture(scope="session", autouse=True)
def _private_display(request: pytest.FixtureRequest) -> None:
    """Browser tests refuse to run outside the display ci.run_native starts."""
    if any("binary" in item.fixturenames for item in request.session.items):
        from ci.browser import require_private_display  # noqa: PLC0415

        require_private_display()


@pytest.fixture(scope="session")
def rounds() -> int:
    return int(os.environ.get("CI_NATIVE_ROUNDS", "3"))


@pytest.fixture(scope="session")
def browsers() -> int:
    return int(os.environ.get("CI_NATIVE_BROWSERS", "3"))
