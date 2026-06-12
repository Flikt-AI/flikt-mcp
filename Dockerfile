# Hosted (remote) Flikt MCP server — runs the Streamable-HTTP server in remote
# mode behind a Cloudflare Tunnel at mcp.flikt.ai. Local stdio users don't need
# this; they `pip install flikt-mcp` and run `python -m flikt_mcp`.
FROM python:3.12-slim

WORKDIR /app

# Install deps first (cache layer), then the package with the remote extra
# (pyjwt[crypto] for Clerk JWT validation; mcp already bundles uvicorn/starlette).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[remote]"

# Remote mode + bind on all interfaces inside the container (reachable only on
# the compose network as mcp:8080; never published to the host/internet).
ENV FLIKT_MCP_REMOTE=1 \
    FLIKT_MCP_HOST=0.0.0.0 \
    FLIKT_MCP_PORT=8080

EXPOSE 8080
CMD ["python", "-m", "flikt_mcp"]
