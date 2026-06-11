"""Flikt MCP server — Claude tools over the Flikt.AI customer API.

Two run modes, same seven tools:

* **Local (stdio)** — ``FLIKT_API_TOKEN=flk_… python -m flikt_mcp``. A single
  customer machine token is sent to api.flikt.ai; the backend enforces scopes
  and the 402 spend-guard. This is the developer / power-user path and what the
  PyPI package does by default.

* **Remote (hosted)** — ``FLIKT_MCP_REMOTE=1 … python -m flikt_mcp`` runs a
  multi-tenant Streamable-HTTP server (mcp.flikt.ai). Claude authenticates each
  user via Clerk OAuth (DCR); every request carries that user's Clerk OAuth JWT,
  which :mod:`flikt_mcp.auth` validates and each tool **forwards** to
  api.flikt.ai. The backend resolves the same user and applies identical tenant
  scoping — the MCP server adds no privilege of its own.

Every tool returns plain JSON-serializable data; errors surface as readable
strings (FliktApiError messages are customer-facing by doctrine).
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from flikt_mcp.client import FliktApiError, FliktClient

_INSTRUCTIONS = (
    "Tools for Flikt.AI construction plan reviews: browse projects, read "
    "detected coordination conflicts, ask questions about results, export "
    "RFIs, and (if the token allows it) start a review of an uploaded plan "
    "set. Reviews take a while — after run_review, poll check_review_status "
    "every few minutes rather than waiting synchronously."
)

_REMOTE = os.environ.get("FLIKT_MCP_REMOTE", "").lower() in ("1", "true", "yes")


def _build_mcp() -> FastMCP:
    """Local mode → plain stdio FastMCP. Remote mode → Streamable-HTTP FastMCP
    wired to validate Clerk OAuth tokens and serve RFC 9728 protected-resource
    metadata."""
    if not _REMOTE:
        return FastMCP("flikt", instructions=_INSTRUCTIONS)

    from mcp.server.auth.settings import AuthSettings

    from flikt_mcp.auth import ClerkTokenVerifier, clerk_issuer

    resource_url = os.environ.get("MCP_RESOURCE_URL", "https://mcp.flikt.ai").rstrip("/")
    # Empty by default to avoid locking out the first real connection before
    # Clerk's emitted scopes are confirmed (Phase 2). run_review is separately
    # gated on SCOPE_RUN below.
    required_scopes = os.environ.get("FLIKT_MCP_REQUIRED_SCOPES", "").split()
    return FastMCP(
        "flikt",
        instructions=_INSTRUCTIONS,
        token_verifier=ClerkTokenVerifier(),
        auth=AuthSettings(
            issuer_url=clerk_issuer(),
            resource_server_url=resource_url,
            required_scopes=required_scopes,
        ),
        host=os.environ.get("FLIKT_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("FLIKT_MCP_PORT", "8080")),
    )


mcp = _build_mcp()

_client: Optional[FliktClient] = None


def _get_client() -> FliktClient:
    """Return the FliktClient for the current call.

    Remote: build a per-request client from the authenticated user's forwarded
    Clerk OAuth token. Local: a process-wide singleton from ``FLIKT_API_TOKEN``.
    This is the seam the server-tool tests monkeypatch.
    """
    if _REMOTE:
        from mcp.server.auth.middleware.auth_context import get_access_token

        access = get_access_token()
        if access is None:
            raise FliktApiError(401, "Not authenticated — reconnect the Flikt connector in Claude.")
        return FliktClient(token=access.token)

    global _client
    if _client is None:
        _client = FliktClient()
    return _client


@asynccontextmanager
async def _client_for_request():
    """Yield the request's FliktClient, closing it afterward in remote mode
    (a fresh per-user client each call). Local mode yields the long-lived
    singleton and leaves it open."""
    client = _get_client()
    try:
        yield client
    finally:
        if _REMOTE:
            await client.aclose()


def _request_has_scope(scope: str) -> bool:
    """True if the current authenticated request carries ``scope``. Always True
    in local mode (the flk_ token's scopes are enforced backend-side)."""
    if not _REMOTE:
        return True
    from mcp.server.auth.middleware.auth_context import get_access_token

    access = get_access_token()
    return bool(access and scope in (access.scopes or []))


def _condense_project(p: dict) -> dict:
    """Trim a project payload to what an agent needs (full payloads carry
    portal-UI fields that just burn context)."""
    keys = (
        "id",
        "name",
        "location",
        "status",
        "submission_status",
        "submission_id",
        "total_pages",
        "total_conflicts",
        "severity_counts",
        "ball_in_court_counts",
        "warning_message",
        "created_at",
    )
    return {k: p[k] for k in keys if k in p}


@mcp.tool()
async def list_projects() -> str:
    """List the Flikt projects this token can access, with open-conflict
    counts by severity and the latest review status per project."""
    async with _client_for_request() as client:
        projects = await client.list_projects()
    return json.dumps([_condense_project(p) for p in projects], indent=2)


@mcp.tool()
async def get_project(project_id: str) -> str:
    """Get one project's summary: review status, page counts, open-conflict
    severity rollup, and the latest submission id (needed for run_review)."""
    async with _client_for_request() as client:
        return json.dumps(_condense_project(await client.get_project(project_id)), indent=2)


@mcp.tool()
async def list_conflicts(
    project_id: str,
    severity: Optional[str] = None,
    discipline: Optional[str] = None,
    ball_in_court: Optional[str] = None,
    max_results: int = 25,
) -> str:
    """List a project's open coordination conflicts (title, severity,
    disciplines, location, sheets, description, recommended action, cost
    impact). Filter by severity ('critical'/'major'/'minor'/'info'),
    discipline, or ball_in_court role. Returns at most max_results conflicts
    plus the project-level summary."""
    async with _client_for_request() as client:
        data: Any = await client.list_conflicts(
            project_id,
            severity=severity,
            discipline=discipline,
            ball_in_court=ball_in_court,
        )
    if isinstance(data, dict) and isinstance(data.get("conflicts"), list):
        total = len(data["conflicts"])
        if total > max_results:
            data["conflicts"] = data["conflicts"][:max_results]
            data["note"] = (
                f"Showing {max_results} of {total} conflicts (sorted most-severe first). "
                "Raise max_results or filter by severity/discipline to see the rest."
            )
    return json.dumps(data, indent=2)


@mcp.tool()
async def ask_project(project_id: str, question: str) -> str:
    """Ask a question about a project's review results — total cost exposure,
    counts by severity/discipline, top risks, schedule impact. Answers come
    straight from the project's conflict data."""
    async with _client_for_request() as client:
        return json.dumps(await client.ask(project_id, question), indent=2)


@mcp.tool()
async def check_review_status(project_id: str) -> str:
    """Check whether a project's review is finished. Returns the latest
    submission status: 'uploaded' (validated, ready to run), 'processing'
    (review in progress — poll again in a few minutes), 'complete' (results
    ready: use list_conflicts / ask_project), or 'failed'."""
    async with _client_for_request() as client:
        p = await client.get_project(project_id)
    return json.dumps(
        {
            "project_id": p.get("id"),
            "submission_id": p.get("submission_id"),
            "submission_status": p.get("submission_status"),
            "progress_estimate_seconds": p.get("progress_estimate_seconds"),
            "warning_message": p.get("warning_message"),
            "error_message": p.get("error_message"),
        },
        indent=2,
    )


@mcp.tool()
async def save_rfis_pdf(project_id: str, save_path: str) -> str:
    """Download the project's RFI package (one ready-to-send RFI per open
    conflict) as a PDF to a local file path."""
    async with _client_for_request() as client:
        pdf = await client.download_bulk_rfis_pdf(project_id)
    path = Path(save_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    return f"Saved RFI package ({len(pdf)} bytes) to {path}"


@mcp.tool()
async def run_review(project_id: str, submission_id: Optional[str] = None) -> str:
    """Start the review for a project's uploaded plan set. Requires a token
    with the 'start reviews' permission and an uploaded (validated)
    submission that the account's subscription fully covers — anything that
    would require payment is refused with instructions. If submission_id is
    omitted, the project's latest submission is used when it is ready to run.
    Reviews take a while: poll check_review_status afterwards."""
    if _RUN_SCOPE_GATING and not _request_has_scope(_RUN_SCOPE):
        return (
            "This connection isn't authorized to start reviews. Reconnect the Flikt "
            "connector and grant the 'start reviews' permission, or start the review "
            "from the Flikt portal."
        )
    async with _client_for_request() as client:
        if submission_id is None:
            p = await client.get_project(project_id)
            if p.get("submission_status") != "uploaded" or not p.get("submission_id"):
                return (
                    f"Project's latest submission is '{p.get('submission_status')}', not ready to "
                    "run. A review can start only from an 'uploaded' (validated) submission — "
                    "upload and validate a plan set in the Flikt portal first, or pass an "
                    "explicit submission_id."
                )
            submission_id = p["submission_id"]
        result = await client.run_review(project_id, submission_id)
    return json.dumps(result, indent=2)


# Resolved lazily so local mode never imports the auth module (and its
# pyjwt/cryptography deps).
_RUN_SCOPE = "reviews:run"
# MCP-layer run-scope gating is OFF by default: Clerk issues only standard OIDC
# scopes (profile/email/offline_access), so there is no custom run scope to
# check, and the backend's 402 spend-guard is the real control on starting
# reviews. Set FLIKT_MCP_GATE_RUN_SCOPE=1 to enforce a custom run scope if one
# is ever configured on the Clerk OAuth app.
_RUN_SCOPE_GATING = os.environ.get("FLIKT_MCP_GATE_RUN_SCOPE", "").lower() in ("1", "true", "yes")
if _REMOTE:
    from flikt_mcp.auth import SCOPE_RUN as _RUN_SCOPE  # noqa: E402


def main() -> None:
    if _REMOTE:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
