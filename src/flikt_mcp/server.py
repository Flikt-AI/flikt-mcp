"""Flikt MCP server — Claude tools over the Flikt.AI customer API.

Run with a machine token minted in the Flikt portal (Settings → API access):

    FLIKT_API_TOKEN=flk_… python -m flikt_mcp

Tool surface mirrors the token scopes:
  read  (default tokens) … list_projects, get_project, list_conflicts,
                           ask_project, check_review_status, save_rfis_pdf
  run   (opt-in scope)   … run_review — starts a review on the customer's
                           subscription page allowance. The backend refuses
                           (HTTP 402) any run that would require payment, so
                           this tool can never spend money, only allowance.

Every tool returns plain JSON-serializable data; errors surface as readable
strings (FliktApiError messages are customer-facing by doctrine).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from flikt_mcp.client import FliktClient

mcp = FastMCP(
    "flikt",
    instructions=(
        "Tools for Flikt.AI construction plan reviews: browse projects, read "
        "detected coordination conflicts, ask questions about results, export "
        "RFIs, and (if the token allows it) start a review of an uploaded plan "
        "set. Reviews take a while — after run_review, poll check_review_status "
        "every few minutes rather than waiting synchronously."
    ),
)

_client: Optional[FliktClient] = None


def _get_client() -> FliktClient:
    global _client
    if _client is None:
        _client = FliktClient()
    return _client


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
    projects = await _get_client().list_projects()
    return json.dumps([_condense_project(p) for p in projects], indent=2)


@mcp.tool()
async def get_project(project_id: str) -> str:
    """Get one project's summary: review status, page counts, open-conflict
    severity rollup, and the latest submission id (needed for run_review)."""
    return json.dumps(_condense_project(await _get_client().get_project(project_id)), indent=2)


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
    data: Any = await _get_client().list_conflicts(
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
    return json.dumps(await _get_client().ask(project_id, question), indent=2)


@mcp.tool()
async def check_review_status(project_id: str) -> str:
    """Check whether a project's review is finished. Returns the latest
    submission status: 'uploaded' (validated, ready to run), 'processing'
    (review in progress — poll again in a few minutes), 'complete' (results
    ready: use list_conflicts / ask_project), or 'failed'."""
    p = await _get_client().get_project(project_id)
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
    pdf = await _get_client().download_bulk_rfis_pdf(project_id)
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
    client = _get_client()
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
