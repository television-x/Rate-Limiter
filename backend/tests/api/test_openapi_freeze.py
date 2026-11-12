"""Contract freeze test for OpenAPI specification.

Per AGENTS.md §9:
- Freeze the API contract snapshot at docs/openapi.json
- Ensure no unexpected schema drifts occur between backend and committed documentation
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.main import create_app

pytestmark = pytest.mark.api


class TestOpenAPIContractFreeze:
    """Verify that the FastAPI runtime OpenAPI schema exactly matches docs/openapi.json."""

    def test_openapi_schema_matches_snapshot(self) -> None:
        app = create_app()
        current_schema = app.openapi()

        # Find openapi.json in docs/
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        snapshot_path = repo_root / "docs" / "openapi.json"
        if not snapshot_path.is_file():
            # Alternative relative path
            snapshot_path = Path("docs") / "openapi.json"

        assert snapshot_path.is_file(), f"OpenAPI snapshot file not found at {snapshot_path}"

        with open(snapshot_path, encoding="utf-8") as f:
            snapshot_schema = json.load(f)

        assert current_schema == snapshot_schema, (
            "OpenAPI schema has drifted from docs/openapi.json snapshot! "
            "If this change is intentional, re-generate docs/openapi.json."
        )
