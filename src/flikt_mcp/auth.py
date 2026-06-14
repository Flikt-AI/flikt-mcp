"""Clerk OAuth token verification for the hosted (remote) MCP server.

Local stdio mode (``FLIKT_API_TOKEN``) never imports this module — it ships
the customer's ``flk_`` machine token straight to api.flikt.ai and the backend
enforces scopes/spend. The hosted ``mcp.flikt.ai`` service runs in REMOTE mode:
Claude drives an OAuth 2.1 + PKCE + Dynamic-Client-Registration flow against
Clerk (the Flikt portal's identity provider), so every MCP request arrives
bearing a Clerk-issued OAuth **access token** — a JWT signed by the same
instance keys, with the same issuer, that the backend already trusts
(``app/api/clerk_auth.py``).

``ClerkTokenVerifier`` validates that JWT (signature via Clerk JWKS, issuer,
expiry, and — once Phase 0 observes the real ``aud`` and we enable it — the
audience/resource binding) and returns an :class:`AccessToken`. The raw token
is carried through on ``AccessToken.token`` so each tool can forward it to
api.flikt.ai, where the backend resolves the same ``user_id`` and applies
identical tenant scoping. The MCP server grants no privilege of its own.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import anyio
import jwt
from jwt import InvalidTokenError, PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier

logger = logging.getLogger("flikt_mcp.auth")

# Scope names. These must match the scopes defined on the Clerk OAuth
# application (Phase 2). Read tools need only the default scope; run_review is
# gated on SCOPE_RUN at the MCP layer (the backend 402 spend-guard applies
# regardless). Overridable so the names can be aligned with Clerk without code.
SCOPE_READ = os.environ.get("FLIKT_MCP_SCOPE_READ", "flikt:read")
SCOPE_RUN = os.environ.get("FLIKT_MCP_SCOPE_RUN", "reviews:run")


def clerk_issuer() -> str:
    iss = os.environ.get("CLERK_ISSUER", "").rstrip("/")
    if not iss:
        raise RuntimeError(
            "CLERK_ISSUER must be set in remote mode (the Clerk instance issuer "
            "URL, e.g. https://clerk.flikt.ai)."
        )
    return iss


def _jwks_url() -> str:
    override = os.environ.get("CLERK_JWKS_URL")
    if not override:
        return f"{clerk_issuer()}/.well-known/jwks.json"
    # A JWKS endpoint on a different origin than the issuer would let an injected
    # env var substitute attacker-controlled signing keys (full JWT-validation
    # bypass). Only honor an override on the issuer's own origin.
    from urllib.parse import urlparse

    iss, ov = urlparse(clerk_issuer()), urlparse(override)
    if (ov.scheme, ov.netloc) != (iss.scheme, iss.netloc):
        raise RuntimeError("CLERK_JWKS_URL origin must match CLERK_ISSUER origin.")
    return override


def _audience_enforced() -> bool:
    """Audience/resource binding is a go-live gate (Phase 4): enable once the
    Phase-0 spike observes the exact ``aud`` Clerk stamps on OAuth access
    tokens. Off by default so the first real connection isn't rejected before
    we know the value to require."""
    return os.environ.get("FLIKT_MCP_VERIFY_AUDIENCE", "false").lower() in ("1", "true", "yes")


# One JWKS client per process; PyJWKClient caches signing keys by ``kid`` and
# refreshes on a miss, so this is networkless after the first verification.
_jwk_client: PyJWKClient | None = None


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        # timeout: a hung Clerk JWKS endpoint must not stall every token
        # verification. lifespan: bound how long a rotated-out signing key may
        # stay cached (else a revoked key lingers for the whole task lifetime).
        _jwk_client = PyJWKClient(_jwks_url(), cache_keys=True, lifespan=3600, timeout=10)
    return _jwk_client


def _extract_scopes(claims: dict[str, Any]) -> list[str]:
    raw = claims.get("scope") or claims.get("scp") or claims.get("scopes") or ""
    if isinstance(raw, str):
        return [s for s in raw.split() if s]
    if isinstance(raw, (list, tuple)):
        return [str(s) for s in raw]
    return []


def _verify_sync(token: str) -> dict[str, Any]:
    """Blocking JWKS fetch + signature/claims validation. Run off the event
    loop via ``anyio.to_thread``."""
    issuer = clerk_issuer()
    signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
    options: dict[str, Any] = {"require": ["exp"]}
    decode_kwargs: dict[str, Any] = {
        "algorithms": ["RS256"],
        "issuer": issuer,
        "options": options,
    }
    if _audience_enforced():
        expected = os.environ.get("MCP_RESOURCE_URL", "").rstrip("/")
        if not expected:
            raise InvalidTokenError("FLIKT_MCP_VERIFY_AUDIENCE is on but MCP_RESOURCE_URL is unset")
        decode_kwargs["audience"] = expected
    else:
        options["verify_aud"] = False
    return jwt.decode(token, signing_key.key, **decode_kwargs)


class ClerkTokenVerifier(TokenVerifier):
    """Validates a Clerk-issued OAuth access-token JWT and returns an
    :class:`AccessToken` carrying the raw token (for forwarding) plus the
    Clerk ``sub`` (user id) and granted scopes."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(_verify_sync, token)
        except InvalidTokenError as e:
            # Log the failure class only — never the token or message fragments.
            logger.info("Rejected MCP token: %s", type(e).__name__)
            return None
        except Exception as e:  # JWKS fetch / key errors — fail closed
            logger.warning("MCP token verification error: %s", type(e).__name__)
            return None

        subject = claims.get("sub")
        if not subject:
            logger.info("Rejected MCP token: no sub claim")
            return None

        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("client_id") or subject),
            scopes=_extract_scopes(claims),
            expires_at=claims.get("exp"),
            subject=str(subject),
            claims=claims,
        )
