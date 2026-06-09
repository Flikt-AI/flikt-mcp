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
from typing import Any, Optional

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
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
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

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise FliktApiError(0, f"Could not reach the Flikt API at {self._base_url}: {e}") from e
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            if not isinstance(detail, str):
                detail = str(detail)
            raise FliktApiError(resp.status_code, _friendly(resp.status_code, detail))
        if resp.headers.get("content-type", "").startswith("application/pdf"):
            return resp.content
        return resp.json()

    # ── Read surface (scopes: projects:read / conflicts:read / ask / rfi:read)

    async def list_projects(self) -> list[dict]:
        return await self._request("GET", "/api/projects")

    async def get_project(self, project_id: str) -> dict:
        return await self._request("GET", f"/api/projects/{project_id}")

    async def list_conflicts(
        self,
        project_id: str,
        *,
        severity: Optional[str] = None,
        conflict_type: Optional[str] = None,
        discipline: Optional[str] = None,
        ball_in_court: Optional[str] = None,
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

    # ── Run rail (scope: reviews:run) ──────────────────────────────────

    async def run_review(self, project_id: str, submission_id: str) -> dict:
        return await self._request("POST", f"/api/projects/{project_id}/submissions/{submission_id}/run")
