# Connect Flikt to Claude (5 minutes, no install)

This lets you ask Claude about your Flikt construction-plan reviews directly —
your projects, detected conflicts, RFIs — using your normal Flikt login. Nothing
to download or install.

You'll need: a Flikt account (the same email/password you use at the Flikt portal)
and Claude (the web app at **claude.ai** or the **Claude Desktop** app).

## Steps

1. Open **claude.ai** (or the **Claude Desktop** app) and go to
   **Settings → Connectors**.
2. Click **Add custom connector**.
3. Fill in:
   - **Name:** `Flikt AI`
   - **Remote MCP server URL:** `https://mcp.flikt.ai/mcp`
4. Click **Advanced settings** and paste this into **OAuth Client ID**:
   ```
   K3y2QQQdhnaa4Smt
   ```
   Leave **OAuth Client Secret** blank — it isn't needed.
5. Click **Add**.
6. Back on the Connectors screen, click **Connect** next to Flikt. A Flikt
   sign-in window opens — log in with your Flikt account and click **Allow**.
7. Done. In a new chat, try: *"List my Flikt projects"* or
   *"What are the top coordination risks for <your project name>?"*

## What Claude can do once connected

- See **your** projects only (Claude can never see another customer's data).
- Read detected conflicts and coordination risks, ask questions about a review,
  and export RFIs.
- Starting a new review still goes through your normal Flikt plan/billing.

## Troubleshooting

- **"Couldn't connect" / asks you to sign in repeatedly:** make sure the URL is
  exactly `https://mcp.flikt.ai/mcp` and the Client ID above is pasted with no
  extra spaces.
- **Don't see your projects:** confirm you signed in with the same Flikt account
  that owns the projects, then click **Connect** again.
- Still stuck? Email support@flikt.ai.

---
*Note: the Client ID above is Flikt's public OAuth client identifier — it is
shared and non-secret, so it is safe to paste into these instructions. Once
Flikt is listed in Anthropic's connector directory, this client-ID step goes
away entirely (users just search "Flikt" and click Connect).*
