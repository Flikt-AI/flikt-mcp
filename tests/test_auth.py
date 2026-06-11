"""ClerkTokenVerifier + remote scope-gate tests.

Self-contained: mints RS256 JWTs with a throwaway key and stubs the JWKS
client, so no Clerk instance or network is needed. Skipped wholesale if the
remote extra (pyjwt[crypto]) isn't installed.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("jwt", reason="remote extra (pyjwt[crypto]) not installed")

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from flikt_mcp import auth  # noqa: E402

ISSUER = "https://clerk.test.example"


@pytest.fixture(scope="module")
def keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return priv_pem, priv.public_key()


@pytest.fixture(autouse=True)
def _wire(monkeypatch, keypair):
    """Point the verifier at our throwaway public key and a known issuer; make
    audience off by default (each test opts in)."""
    _, public_key = keypair
    monkeypatch.setenv("CLERK_ISSUER", ISSUER)
    monkeypatch.delenv("FLIKT_MCP_VERIFY_AUDIENCE", raising=False)

    class _FakeSigningKey:
        key = public_key

    class _FakeJWKClient:
        def get_signing_key_from_jwt(self, token):  # noqa: ARG002
            return _FakeSigningKey()

    monkeypatch.setattr(auth, "_get_jwk_client", lambda: _FakeJWKClient())


def _mint(priv_pem, **overrides):
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "sub": "user_123",
        "exp": now + 3600,
        "iat": now,
        "scope": "flikt:read",
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not _OMIT}
    return jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "test"})


_OMIT = object()


class TestVerify:
    @pytest.mark.asyncio
    async def test_valid_token(self, keypair):
        priv, _ = keypair
        tok = _mint(priv)
        at = await auth.ClerkTokenVerifier().verify_token(tok)
        assert at is not None
        assert at.subject == "user_123"
        assert at.token == tok  # raw token carried for forwarding
        assert "flikt:read" in at.scopes

    @pytest.mark.asyncio
    async def test_wrong_issuer_rejected(self, keypair):
        priv, _ = keypair
        assert await auth.ClerkTokenVerifier().verify_token(_mint(priv, iss="https://evil.example")) is None

    @pytest.mark.asyncio
    async def test_expired_rejected(self, keypair):
        priv, _ = keypair
        assert await auth.ClerkTokenVerifier().verify_token(_mint(priv, exp=int(time.time()) - 10)) is None

    @pytest.mark.asyncio
    async def test_missing_sub_rejected(self, keypair):
        priv, _ = keypair
        assert await auth.ClerkTokenVerifier().verify_token(_mint(priv, sub=_OMIT)) is None

    @pytest.mark.asyncio
    async def test_garbage_token_rejected(self):
        assert await auth.ClerkTokenVerifier().verify_token("not-a-jwt") is None

    @pytest.mark.asyncio
    async def test_scope_list_form(self, keypair):
        priv, _ = keypair
        tok = _mint(priv, scope=_OMIT, scp=["flikt:read", "reviews:run"])
        at = await auth.ClerkTokenVerifier().verify_token(tok)
        assert at is not None and "reviews:run" in at.scopes


class TestAudienceBinding:
    @pytest.mark.asyncio
    async def test_audience_match_accepted(self, keypair, monkeypatch):
        priv, _ = keypair
        monkeypatch.setenv("FLIKT_MCP_VERIFY_AUDIENCE", "true")
        monkeypatch.setenv("MCP_RESOURCE_URL", "https://mcp.flikt.ai")
        at = await auth.ClerkTokenVerifier().verify_token(_mint(priv, aud="https://mcp.flikt.ai"))
        assert at is not None

    @pytest.mark.asyncio
    async def test_audience_mismatch_rejected(self, keypair, monkeypatch):
        priv, _ = keypair
        monkeypatch.setenv("FLIKT_MCP_VERIFY_AUDIENCE", "true")
        monkeypatch.setenv("MCP_RESOURCE_URL", "https://mcp.flikt.ai")
        at = await auth.ClerkTokenVerifier().verify_token(_mint(priv, aud="https://somewhere.else"))
        assert at is None


class TestRunScopeGate:
    @pytest.mark.asyncio
    async def test_run_review_blocked_without_scope(self, monkeypatch):
        from flikt_mcp import server

        monkeypatch.setattr(server, "_request_has_scope", lambda scope: False)
        out = await server.run_review("p1")
        assert "isn't authorized to start reviews" in out

    @pytest.mark.asyncio
    async def test_run_review_allowed_with_scope(self, monkeypatch):
        from flikt_mcp import server

        class FakeClient:
            async def get_project(self, project_id):
                return {"id": project_id, "submission_status": "processing", "submission_id": "s1"}

            async def aclose(self):
                pass

        monkeypatch.setattr(server, "_request_has_scope", lambda scope: True)
        monkeypatch.setattr(server, "_get_client", lambda: FakeClient())
        out = await server.run_review("p1")
        assert "not ready to run" in out  # scope passed; refused only for readiness
