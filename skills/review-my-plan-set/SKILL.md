---
name: review-my-plan-set
description: Run a full Flikt.AI plan review end-to-end — verify the plan set is ready, confirm the page spend, start the review, poll until complete, then deliver a top-risks summary and the RFI package. Use when the user asks to "review my plans", "run Flikt on this project", or "check my drawings for conflicts".
---

# Review my plan set (Flikt.AI agentic loop)

You drive a complete construction plan review through the `flikt` MCP
tools. The review itself runs on Flikt's side and takes real time
(often 20–60+ minutes for large sets) — your job is orchestration,
spend confirmation, and a decision-ready readout at the end.

## Steps

1. **Locate the project.** `list_projects`; match the user's description
   (name, recency). If ambiguous, ask which project they mean.

2. **Check readiness.** `get_project` → look at `submission_status`:
   - `uploaded` — validated and ready to run. Continue.
   - `processing` — a review is already running; skip to step 5.
   - `complete` — results already exist; ask whether they want the readout
     (step 6) or a fresh run of a newer upload.
   - anything else — the plan set isn't ready. Tell the user to upload and
     validate it in the Flikt portal first; you cannot upload for them.

3. **Confirm the spend (always, even though the API would allow it
   silently).** Tell the user the page count (`total_pages`) and that the
   run will consume that many pages of their subscription allowance, then
   get an explicit yes. Never start a review the user hasn't confirmed in
   this conversation.

4. **Start it.** `run_review` with the project id (it resolves the latest
   uploaded submission itself). If it comes back with a refusal (payment
   required, insufficient allowance, token cap), relay the message
   verbatim — it contains the fix — and stop.

5. **Poll patiently.** `check_review_status` — if `processing`, wait and
   re-check every few minutes (use `progress_estimate_seconds` to set
   expectations; don't poll more than once a minute). Report `failed`
   with its `error_message` and stop.

6. **Deliver the readout.** When `complete`:
   - `ask_project` with "what are the top risks" and "what's my total cost
     exposure" for the headline numbers.
   - `list_conflicts` filtered `severity="critical"` (then `major` if the
     critical list is short) for specifics worth narrating.
   - Summarize: total open conflicts by severity, cost exposure range, the
     3–5 most consequential conflicts (title, location, why it matters,
     recommended action), and any `warning_message`.
   - Offer the RFI package: on yes, `save_rfis_pdf` to a path the user
     names (default `./flikt-rfis-<project-name>.pdf`) and tell them where
     it landed.

## Rules

- **One explicit human confirmation per started review.** No batch-running
  reviews across projects without per-project confirmation.
- Relay 402 refusals verbatim; never retry a refused run unchanged.
- Don't promise wall-clock times; reviews scale with page count.
- Results vocabulary: conflicts, severities, cost impact, RFIs — exactly
  what the tools return. Don't speculate about how Flikt finds conflicts.
