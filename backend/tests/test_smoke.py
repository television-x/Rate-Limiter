"""Trivial smoke test to verify the test toolchain works.

Phase 0 acceptance: this test passes under `make test`.
"""

import pytest


@pytest.mark.unit
def test_smoke() -> None:
    """Verify pytest discovers and runs tests."""
    assert 1 + 1 == 2
