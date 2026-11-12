"""Authentication and authorization dependencies.

Per AGENTS.md §9:
- Admin authentication via Bearer token.
- Constant-time token comparison (secrets.compare_digest) to prevent timing attacks.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

# Security scheme for OpenAPI docs (auto_error=False so we control the exact 401 response)
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_settings(request: Request) -> Settings:
    """Retrieve settings from application state or cached settings."""
    return getattr(request.app.state, "settings", None) or get_settings()


async def verify_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_current_settings),
) -> str:
    """Verify that the incoming Bearer token matches the configured admin token.

    Uses constant-time comparison (secrets.compare_digest) to defend
    against timing side-channel attacks.

    Raises:
        HTTPException: 401 Unauthorized if token is missing or invalid.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expected_token = settings.admin_token
    token_valid = secrets.compare_digest(credentials.credentials, expected_token)

    if not token_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials

