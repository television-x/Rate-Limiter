"""Client identity extraction and trusted proxy evaluation.

Implements identity resolution per AGENTS.md §3.4:
1. API key header ('X-Api-Key') -> IdentityKind.API_KEY
2. 'X-User-Id' (only when DEMO_MODE=true) -> IdentityKind.USER
3. Client IP address (with trusted proxy verification) -> IdentityKind.IP

Malformed identities (overlong, control characters) trigger InvalidIdentity.
"""

from __future__ import annotations

import re

from fastapi import Request

from app.config import Settings, get_settings
from app.core.errors import InvalidIdentity
from app.core.models import IdentityKind, Subject

_MAX_IDENTITY_LENGTH = 128
_CONTROL_CHAR_REGEX = re.compile(r"[\x00-\x1f\x7f]")


def validate_identity_string(value: str) -> None:
    """Validate an identity string against length and character constraints.

    Raises:
        InvalidIdentity: If the string is empty, too long, or contains control chars.
    """
    if not value or not value.strip():
        raise InvalidIdentity("Identity value must not be empty or blank")
    if len(value) > _MAX_IDENTITY_LENGTH:
        raise InvalidIdentity(
            f"Identity exceeds maximum length of {_MAX_IDENTITY_LENGTH} characters"
        )
    if _CONTROL_CHAR_REGEX.search(value):
        raise InvalidIdentity("Identity contains disallowed control characters")


def extract_client_ip(request: Request, settings: Settings) -> str:
    """Extract client IP, trusting forwarded headers only from trusted proxies."""
    direct_peer = request.client.host if request.client else "127.0.0.1"

    if settings.is_proxy_trusted(direct_peer):
        # Trusted proxy: inspect X-Forwarded-For or X-Real-IP
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            # First IP in comma-separated chain is the original client
            client_ip = forwarded_for.split(",")[0].strip()
            if client_ip:
                return client_ip

        real_ip = request.headers.get("x-real-ip")
        if real_ip and real_ip.strip():
            return real_ip.strip()

    return direct_peer


def extract_identity(
    request: Request,
    settings: Settings | None = None,
) -> Subject:
    """Extract and validate the client identity from the request.

    Priority:
    1. X-Api-Key header (API key)
    2. X-User-Id header (only if DEMO_MODE=True)
    3. Client IP (from direct peer or trusted proxy headers)

    Raises:
        InvalidIdentity: If an explicit identity header is malformed.
    """
    if settings is None:
        settings = get_settings()

    # 1. API Key Header
    api_key = request.headers.get("x-api-key")
    if api_key is not None:
        validate_identity_string(api_key)
        return Subject(kind=IdentityKind.API_KEY, identity=api_key.strip())

    # 2. X-User-Id Header (only when demo_mode is True)
    if settings.demo_mode:
        user_id = request.headers.get("x-user-id")
        if user_id is not None:
            validate_identity_string(user_id)
            return Subject(kind=IdentityKind.USER, identity=user_id.strip())

    # 3. Client IP Address
    client_ip = extract_client_ip(request, settings)
    validate_identity_string(client_ip)
    return Subject(kind=IdentityKind.IP, identity=client_ip)
