"""Flikt API client for the MCP server.

A thin, SDK-independent async HTTP layer over api.flikt.ai. Every method
maps backend errors to ``FliktApiError`` with a message safe to surface
verbatim in an agent conversation (the backend's own ``detail`` strings are
customer-facing by doctrine).

Auth: a customer-minted machine token (portal → Settings → API access),
sent as ``Authorization: Bearer flk_…``. The backend enforces scopes and
spend guards server-side; this client adds no privilege of its own.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.flikt.ai"
_TIMEOUT_S = 60.0


class FliktApiError(Exception):
    """API-level failure with an agent-surfaceable message."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def _friendly(status_code: int, detail: str) -> str:
    if status_code == 401:
        return (
            "The Flikt API token was rejected (invalid, revoked, or not allowed on this "
            "operation). Check FLIKT_API_TOKEN, or mint a new token in the portal under "
            "Settings → API access."
        )
    if status_code == 402:
        # Spend guard — pass the backend's actionable message through.
        return detail
    if status_code == 404:
        return "Not found — check the project id (use list_projects to see what this token can access)."
    if status_code == 429:
        return "Rate limited by the Flikt API — wait a moment and retry."
    return f"Flikt API error ({status_code}): {detail}"


class FliktClient:
    def __init__(self, token: str | None = None, base_url: str | None = None):
        self._token = token or os.environ.get("FLIKT_API_TOKEN", "")
        self._base_url = (base_url or os.environ.get("FLIKT_API_BASE", DEFAULT_BASE_URL)).rstrip("/")
        if not self._token:
            raise FliktApiError(
                0,
                "FLIKT_API_TOKEN is not set. Mint a token in the Flikt portal "
                "(Settings → API access) and export it before starting the server.",
            )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=_TIMEOUT_S,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _unreachable(e: httpx.HTTPError) -> FliktApiError:
        # Don't echo the base URL (it can be an internal host) or raw error.
        return FliktApiError(
            0, f"Could not reach the Flikt API ({type(e).__name__}). Check your connection and retry."
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise self._unreachable(e) from e
        self._raise_for_error(resp)
        if resp.headers.get("content-type", "").startswith("application/pdf"):
            return resp.content
        return resp.json()

    @staticmethod
    def _raise_for_error(resp: httpx.Response) -> None:
        """Map a 4xx/5xx to ``FliktApiError``. The body must already be read."""
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            # The blind-except suppression below is deliberate. This is the
            # error path, and its only job is to end in a FliktApiError
            # carrying something a person can read. The two failures we can name are a non-JSON body
            # (json.JSONDecodeError, a ValueError) and a JSON body that is not
            # an object, so .get does not exist (AttributeError) — a 502 HTML
            # page from a proxy hits the first, a bare JSON array the second.
            # Narrowing to those two would be accurate today and would turn
            # any third case into an unhandled exception surfacing mid-
            # conversation to an agent's user, in exchange for nothing: there
            # is no recovery here that depends on WHICH parse failed. The
            # fallback is the raw text, which is always available.
            except Exception:  # noqa: BLE001 — any unparseable body falls back to text
                detail = resp.text
            if not isinstance(detail, str):
                detail = str(detail)
            raise FliktApiError(resp.status_code, _friendly(resp.status_code, detail))

    async def _stream_to(self, path: str, write: Callable[[bytes], object]) -> int:
        """GET ``path`` and hand the body to ``write`` chunk by chunk.

        Returns the byte count. Peak memory is one chunk, not the body: in
        remote mode every tenant's export is in flight at once, so a buffered
        body stacks per tenant (DEBT-MCP-BULK-RFI-PDF-BUFFERED). Errors map
        exactly as ``_request`` maps them; an error body is small, so it is read
        whole before mapping.
        """
        try:
            async with self._client.stream("GET", path) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    self._raise_for_error(resp)
                total = 0
                async for chunk in resp.aiter_bytes():
                    write(chunk)
                    total += len(chunk)
                return total
        except httpx.HTTPError as e:
            raise self._unreachable(e) from e

    # ── Read surface (scopes: projects:read / conflicts:read / ask / rfi:read)

    async def list_projects(self) -> list[dict]:
        return await self._request("GET", "/api/projects")

    async def get_project(self, project_id: str) -> dict:
        return await self._request("GET", f"/api/projects/{project_id}")

    async def list_conflicts(
        self,
        project_id: str,
        *,
        severity: str | None = None,
        conflict_type: str | None = None,
        discipline: str | None = None,
        ball_in_court: str | None = None,
        status: str = "open",
    ) -> Any:
        params = {
            k: v
            for k, v in {
                "severity": severity,
                "conflict_type": conflict_type,
                "discipline": discipline,
                "ball_in_court": ball_in_court,
                "status": status,
            }.items()
            if v is not None
        }
        return await self._request("GET", f"/api/projects/{project_id}/conflicts", params=params)

    async def ask(self, project_id: str, question: str) -> dict:
        return await self._request("GET", f"/api/projects/{project_id}/ask", params={"q": question})

    async def download_bulk_rfis_pdf(self, project_id: str) -> bytes:
        return await self._request("GET", f"/api/projects/{project_id}/rfis.pdf")

    async def stream_bulk_rfis_pdf(self, project_id: str, write: Callable[[bytes], object]) -> int:
        """The bulk RFI PDF, streamed into ``write``; returns its size in bytes."""
        return await self._stream_to(f"/api/projects/{project_id}/rfis.pdf", write)

    # ── Run rail (scope: reviews:run) ──────────────────────────────────

    async def run_review(self, project_id: str, submission_id: str) -> dict:
        return await self._request("POST", f"/api/projects/{project_id}/submissions/{submission_id}/run")
