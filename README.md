# flikt-mcp

MCP server that connects Claude (Claude Code, Claude Desktop, or any MCP
client) to your [Flikt.AI](https://flikt.ai) account: browse plan-review
results, dig into coordination conflicts, ask questions, export RFI
packages, and — if you allow it — kick off reviews of uploaded plan sets.

## Setup

1. **Mint a token** in the Flikt portal: **Settings → API access → Create
   token**. Default tokens are read-only. Check *"Allow this token to start
   reviews"* (and set a monthly page limit) only if you want Claude to be
   able to start reviews against your subscription's page allowance — a
   token can never charge your card; runs that aren't fully covered by your
   plan are refused.
2. Copy the token at creation — it is shown exactly once.
3. **Add the server** (Claude Code):

   ```bash
   claude mcp add flikt \
     --env FLIKT_API_TOKEN=flk_your_token_here \
     -- python -m flikt_mcp
   ```

   or in any MCP client config:

   ```json
   {
     "mcpServers": {
       "flikt": {
         "command": "python",
         "args": ["-m", "flikt_mcp"],
         "env": { "FLIKT_API_TOKEN": "flk_your_token_here" }
       }
     }
   }
   ```

   Install first with `pip install .` (from this directory), or run via
   `uvx --from <path-to-flikt-mcp> flikt-mcp`.

`FLIKT_API_BASE` overrides the API host (default `https://api.flikt.ai`).

## Tools

| Tool | Token permission | What it does |
|---|---|---|
| `list_projects` | read | Projects with open-conflict severity rollups + review status |
| `get_project` | read | One project's summary + latest submission id |
| `list_conflicts` | read | Open conflicts (filter by severity / discipline / ball-in-court) |
| `ask_project` | read | Q&A over results: cost exposure, counts, top risks, schedule impact |
| `check_review_status` | read | Poll a running review (`uploaded` → `processing` → `complete`) |
| `save_rfis_pdf` | read | Download the ready-to-send RFI package to a local file |
| `run_review` | **start reviews** | Start the review for an uploaded, fully-covered submission |

## Spend safety

`run_review` rides your subscription's page allowance and nothing else.
The Flikt API refuses (HTTP 402) any run that would require a payment —
overage, per-project pricing, or spec add-ons — and tells you to finish in
the portal instead. Per-token monthly page limits (set at mint time) bound
what a single token can consume; revoking a token in Settings cuts access
instantly.

## Agentic review skill

`skills/review-my-plan-set/SKILL.md` packages the full loop — check the
plan set, confirm the spend with you, start the review, poll, then deliver
a top-risks summary and the RFI package. Point your Claude skills directory
at it or copy it into `.claude/skills/`.

## Development

```bash
pip install -e ".[dev]"
pytest
```
