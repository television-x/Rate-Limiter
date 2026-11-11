# ADR 0005: Client Identity Trust and Resolution Hierarchy

## Status

Accepted (Phase 6)

## Context

Distributed rate limiters aggregate state per client identity (user ID, API key, IP address). If client identity extraction is vulnerable to spoofing, attackers can bypass rate limits by forging headers (e.g., injecting random `X-Forwarded-For` IPs) or flood storage by supplying massive random strings as identities.

Furthermore, in development and demonstration environments, testers need to simulate multiple distinct users from a single client without registering real credentials.

## Decision

We implement a three-tiered identity extraction hierarchy with strict proxy validation and input constraints (AGENTS.md §3.4):

1. **Resolution Priority:**
   1. `X-Api-Key` header (`IdentityKind.API_KEY`)
   2. `X-User-Id` header (`IdentityKind.USER`) — **strictly gated by `DEMO_MODE=true`**
   3. Client IP address (`IdentityKind.IP`)

2. **Proxy Trust Enforcement:**
   - Headers such as `X-Forwarded-For` and `X-Real-IP` are inspected **only** if the direct network peer IP is listed in `TRUSTED_PROXIES` (e.g. reverse proxy / Nginx container).
   - If the direct peer is not trusted, `X-Forwarded-For` is completely ignored and the direct peer IP is used as the identity.
   - When trusted, only the first client IP in the comma-separated chain is extracted.

3. **Input Validation and Length Bounding:**
   - All identity strings must be non-empty, non-whitespace, and at most 128 characters.
   - Control characters (ASCII `\x00-\x1f`, `\x7f`) are disallowed.
   - Any violation raises typed `InvalidIdentity` domain error, resulting in an immediate `400 Bad Request` with `application/problem+json`.

4. **Storage Key Hashing:**
   - Extracted identities are hashed via SHA-256 (truncated to 16 hex characters) in `app.core.key_builder`, preventing delimiter injection and bounding storage memory footprint regardless of identity type.

## Consequences

### Positive
- **Spoofing resistance:** Untrusted direct clients cannot evade rate limits by fabricating `X-Forwarded-For` headers.
- **Controlled demo flexibility:** `X-User-Id` enables full multi-user testing in local/staging environments while remaining safely disabled in production.
- **Resource protection:** Enforcing length and character bounds prevents delimiter attacks and excessive Redis memory allocation.

### Negative / Trade-offs
- Misconfiguration of `TRUSTED_PROXIES` in production could result in all clients sharing the proxy's IP if the proxy IP is omitted from the trusted list. Documentation and `.env.example` must highlight this setting.
