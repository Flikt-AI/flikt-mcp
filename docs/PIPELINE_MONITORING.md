# Following the Customer Portal Pipeline (Administrator View)

> **Status:** Design / recommendation. This document was drafted from the
> `flikt-mcp` repo and the public org metadata only. The backend and pipeline
> source (`flikt-ai-backend`, `flikt-pipeline`, `flikt-customer-portal`) were
> **not accessible** when this was written, so the specifics below (queue
> mechanism, log group names, DB schema) need to be confirmed against those
> repos before implementation. See [Open questions](#open-questions).

## The question

> "How can I check and follow live what is happening in the customer portal
> pipeline from an administrator perspective — across the entire backend, at a
> moment's notice?"

## What exists today (and why it isn't enough)

`flikt-mcp` (this repo) is a thin **MCP client** in front of the Flikt API. It
can report pipeline status, but only:

- **Per user / per tenant** — every call carries one user's OAuth token and the
  backend scopes results to that user (`src/flikt_mcp/auth.py`). There is no
  cross-customer view.
- **Pull-based** — status comes from polling `check_review_status` /
  `get_project` (`src/flikt_mcp/server.py`). There is no push / live stream.

So the MCP layer is the wrong place for backend-wide monitoring. It answers
"how is *my* review going," not "what is happening across the whole system
right now."

## The actual architecture

```
 customer ──▶ flikt-customer-portal (app.flikt.ai, Next.js)
                     │
                     ▼
              flikt-ai-backend (API + job orchestration + submission state)
                     │  enqueues work
                     ▼
              flikt-pipeline  ──▶  on-demand EC2 workers
                                   (run from the flikt-pipeline ECR image)
                                   = conflict-detection engine

 flikt-mcp ── side client (Claude), per-user, read/control only
```

The **pipeline** is the conflict-detection workload that runs as **ephemeral
EC2 workers launched from a container image (ECR)**. That single fact drives the
whole monitoring design: because workers are short-lived cloud machines, "watch
the pipeline live" is largely an **AWS observability** problem, plus a
**backend status** problem for the business-level view.

### Pipeline state machine (per submission)

```
uploaded ──▶ processing ──▶ complete
                       └──▶ failed
```

Each submission also carries `progress_estimate_seconds`, `warning_message`,
and `error_message` (surfaced today via the MCP tools).

## Recommended approach — two complementary layers

### Layer 1 — AWS health dashboard + alerts (the "control room")

What it is: a CloudWatch dashboard built from signals the workers already emit,
plus alarms that notify you (email/Slack/SMS) when something breaks. Reads
existing data — **little or no product change**.

Gives you, at a glance:

- Jobs in flight right now / queue depth
- Worker count and lifecycle (launching / running / terminating)
- Job durations and failure rate over time
- Recent errors (CloudWatch Logs Insights query)

And **alerts** for: failure-rate spike, jobs stuck in `processing` beyond N
minutes, no workers available while a queue is backing up.

Best for: you or ops confirming the machinery is healthy and being paged when
it isn't.

### Layer 2 — Backend admin view (the everyday screen)

What it is: an authenticated **admin-only** page/endpoint in the backend (or
portal) that lists **every customer's submissions and their current stage** in
plain business terms — who, what plan, which stage, how long, any error.

Gives you: "show me all orders and their status" without touching AWS consoles.
This is the screen a non-technical administrator will actually use day-to-day.

Best for: at-a-glance, cross-tenant business visibility.

Requires building: an admin auth gate + a query over the submissions table +
a simple page. **Start read-only.** A later enhancement can make it update
live (Server-Sent Events or short polling) so it follows the pipeline without
a manual refresh.

### Recommendation

Build **Layer 2** as the primary deliverable (it's what you'll use), and stand
up **Layer 1** alongside it because it's cheap and it's what catches problems
before customers do. Defer live-push (SSE) until the static admin view proves
useful — short polling every few seconds is enough to start.

## Next steps (require backend / pipeline repo access)

1. **Confirm the queue + state store** in `flikt-ai-backend`: how jobs are
   enqueued, where submission status lives, what an admin query would look like.
2. **Confirm worker observability** in `flikt-pipeline`: what the workers log,
   to which CloudWatch log group, and what metrics (if any) they already emit.
   Identify the smallest set of custom metrics worth adding.
3. **Design the admin gate**: how admins are identified (Clerk role/claim?) so
   the cross-tenant view is locked down.
4. Produce the CloudWatch dashboard + alarm definitions (Layer 1) and the admin
   view spec (Layer 2), then implement.

## Open questions

- What queue/orchestration does `flikt-ai-backend` use (SQS, DB-polling, Step
  Functions, …)?
- Where is submission status persisted, and is there already an internal/admin
  API?
- What do `flikt-pipeline` workers log today, and is there existing CloudWatch
  dashboarding / alarming?
- Who counts as an "administrator," and how is that authorized?

## Access note

This design is necessarily high-level because this session is scoped to
`flikt-mcp` only. To turn it into a concrete implementation plan (and code),
the session needs access to `flikt-ai-backend`, `flikt-pipeline`, and
`flikt-customer-portal` — either by starting a session scoped to those repos or
by adding them to this environment's repository configuration.
