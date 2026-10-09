"""FliktClient tests — httpx.MockTransport, no network, no MCP SDK needed."""

from __future__ import annotations

import json

import httpx
import pytest

from flikt_mcp.client import DEFAULT_BASE_URL, FliktApiError, FliktClient


def _client_with(handler) -> FliktClient:
    c = FliktClient(token="flk_testtoken", base_url="https://api.test")
    c._client = httpx.AsyncClient(
        base_url="https://api.test",
        headers={"Authorization": "Bearer flk_testtoken"},
        transport=httpx.MockTransport(handler),
    )
    return c


class TestConstruction:
    def test_missing_token_fails_loud(self, monkeypatch):
        monkeypatch.delenv("FLIKT_API_TOKEN", raising=False)
        with pytest.raises(FliktApiError, match="FLIKT_API_TOKEN"):
            FliktClient()

    def test_env_token_and_default_base(self, monkeypatch):
        monkeypatch.setenv("FLIKT_API_TOKEN", "flk_env")
        c = FliktClient()
        assert c._base_url == DEFAULT_BASE_URL
        assert c._client.headers["Authorization"] == "Bearer flk_env"

    def test_base_url_env_override(self, monkeypatch):
        monkeypatch.setenv("FLIKT_API_TOKEN", "flk_env")
        monkeypatch.setenv("FLIKT_API_BASE", "https://staging.example/")
        assert FliktClient()._base_url == "https://staging.example"


class TestRequests:
    @pytest.mark.asyncio
    async def test_bearer_header_sent(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json=[])

        c = _client_with(handler)
        await c.list_projects()
        assert seen["auth"] == "Bearer flk_testtoken"

    @pytest.mark.asyncio
    async def test_list_conflicts_passes_filters(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"conflicts": []})

        c = _client_with(handler)
        await c.list_conflicts("p1", severity="critical", discipline="Mechanical")
        assert seen["params"]["severity"] == "critical"
        assert seen["params"]["discipline"] == "Mechanical"
        assert seen["params"]["status"] == "open"
        assert "ball_in_court" not in seen["params"]  # None filters omitted

    @pytest.mark.asyncio
    async def test_ask_urlencodes_question(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["q"] = request.url.params.get("q")
            return httpx.Response(200, json={"matched": True})

        c = _client_with(handler)
        await c.ask("p1", "what's my total cost exposure?")
        assert seen["q"] == "what's my total cost exposure?"

    @pytest.mark.asyncio
    async def test_pdf_content_returned_as_bytes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-fake", headers={"content-type": "application/pdf"})

        c = _client_with(handler)
        pdf = await c.download_bulk_rfis_pdf("p1")
        assert pdf.startswith(b"%PDF-")

    @pytest.mark.asyncio
    async def test_run_review_posts(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            return httpx.Response(202, json={"status": "queued"})

        c = _client_with(handler)
        out = await c.run_review("p1", "s1")
        assert seen["method"] == "POST"
        assert seen["path"] == "/api/projects/p1/submissions/s1/run"
        assert out["status"] == "queued"


class TestErrorMapping:
    @staticmethod
    def _erroring(status: int, detail: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"detail": detail})

        return _client_with(handler)

    @pytest.mark.asyncio
    async def test_401_token_guidance(self):
        c = self._erroring(401, "Invalid or revoked API token.")
        with pytest.raises(FliktApiError, match="Settings → API access"):
            await c.list_projects()

    @pytest.mark.asyncio
    async def test_402_passes_backend_message_verbatim(self):
        detail = "Subscription page budget is insufficient for this run (150 pages needed, 100 remaining this cycle)."
        c = self._erroring(402, detail)
        with pytest.raises(FliktApiError) as exc:
            await c.run_review("p1", "s1")
        assert str(exc.value) == detail
        assert exc.value.status_code == 402

    @pytest.mark.asyncio
    async def test_404_friendly(self):
        c = self._erroring(404, "Not found")
        with pytest.raises(FliktApiError, match="list_projects"):
            await c.get_project("nope")

    @pytest.mark.asyncio
    async def test_network_error_does_not_leak_host(self):
        """Unreachable-API errors must name the failure class, not the base URL
        (which can be an internal host) — SEC-07."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        c = _client_with(handler)
        with pytest.raises(FliktApiError) as exc:
            await c.list_projects()
        msg = str(exc.value)
        assert "ConnectError" in msg
        assert "api.test" not in msg  # base URL not leaked
        assert "boom" not in msg  # raw exception text not leaked

    @pytest.mark.asyncio
    async def test_non_json_error_body_tolerated(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="<html>gateway</html>")

        c = _client_with(handler)
        with pytest.raises(FliktApiError, match="500"):
            await c.list_projects()


class TestServerTools:
    """Smoke the FastMCP layer: tool registration + the condense/truncate logic.
    Skipped wholesale when the mcp SDK isn't installed."""

    @pytest.fixture(autouse=True)
    def _require_mcp(self):
        pytest.importorskip("mcp")

    def test_all_tools_registered(self):
        from flikt_mcp import server

        # FastMCP keeps a tool manager; assert our seven tools are present.
        names = {t.name for t in server.mcp._tool_manager.list_tools()}
        assert names == {
            "list_projects",
            "get_project",
            "list_conflicts",
            "ask_project",
            "check_review_status",
            "save_rfis_pdf",
            "run_review",
        }

    def test_all_tools_have_directory_annotations(self):
        """Connectors-directory hard gate: every tool must carry a title and
        either readOnlyHint or destructiveHint (missing annotations are the #1
        rejection reason). Lock it so a new tool can't ship un-annotated."""
        from flikt_mcp import server

        for t in server.mcp._tool_manager.list_tools():
            ann = t.annotations
            assert ann is not None, f"{t.name}: missing annotations"
            assert ann.title, f"{t.name}: missing annotations.title"
            assert (ann.readOnlyHint is not None) or (ann.destructiveHint is not None), (
                f"{t.name}: needs readOnlyHint or destructiveHint"
            )

    def test_read_tools_are_read_only(self):
        """The five browse/ask tools must declare readOnlyHint=True; the two
        write/action tools (export, run) must not."""
        from flikt_mcp import server

        ro = {t.name: t.annotations.readOnlyHint for t in server.mcp._tool_manager.list_tools()}
        for name in ("list_projects", "get_project", "list_conflicts", "ask_project", "check_review_status"):
            assert ro[name] is True, f"{name} should be readOnlyHint=True"
        for name in ("save_rfis_pdf", "run_review"):
            assert ro[name] is False, f"{name} should not be readOnlyHint=True"

    @pytest.mark.asyncio
    async def test_list_conflicts_truncates(self, monkeypatch):
        from flikt_mcp import server

        class FakeClient:
            async def list_conflicts(self, project_id, **kw):
                return {"conflicts": [{"id": str(i)} for i in range(40)], "summary": {}}

        monkeypatch.setattr(server, "_get_client", lambda: FakeClient())
        out = json.loads(await server.list_conflicts("p1", max_results=25))
        assert len(out["conflicts"]) == 25
        assert "Showing 25 of 40" in out["note"]

    @pytest.mark.asyncio
    async def test_ask_project_rejects_overlong_question(self):
        """Cap forwarded question length (DoS / prompt-injection forward)."""
        from flikt_mcp import server

        out = await server.ask_project("p1", "x" * 2001)
        assert "too long" in out.lower()

    @pytest.mark.asyncio
    async def test_save_rfis_pdf_refuses_path_escape(self, monkeypatch):
        """Local-mode write must not escape the user's home tree."""
        from flikt_mcp import server

        class FakeClient:
            async def stream_bulk_rfis_pdf(self, project_id, write):
                write(b"%PDF-1.4 fake")
                return len(b"%PDF-1.4 fake")

        monkeypatch.setattr(server, "_get_client", lambda: FakeClient())
        out = await server.save_rfis_pdf("p1", "/etc/cron.d/evil")
        assert "Refusing to write outside" in out

    @pytest.mark.asyncio
    async def test_run_review_refuses_unready_submission(self, monkeypatch):
        from flikt_mcp import server

        class FakeClient:
            async def get_project(self, project_id):
                return {"id": project_id, "submission_status": "processing", "submission_id": "s1"}

        monkeypatch.setattr(server, "_get_client", lambda: FakeClient())
        out = await server.run_review("p1")
        assert "not ready to run" in out


# ── the bulk RFI PDF is streamed, never held whole (DEBT-MCP-BULK-RFI-PDF-BUFFERED)

_CHUNK = 64 * 1024


def _pdf_handler(n_chunks: int):
    """A PDF body of ``n_chunks`` x 64 KiB, produced lazily, as a real stream is."""

    async def body():
        for _ in range(n_chunks):
            yield b"\0" * _CHUNK

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers={"content-type": "application/pdf"})

    return handler


class TestStreamedPdf:
    @pytest.mark.asyncio
    async def test_stream_hands_over_every_byte_and_returns_the_size(self):
        got = bytearray()
        c = _client_with(_pdf_handler(3))
        size = await c.stream_bulk_rfis_pdf("p1", got.extend)
        assert size == 3 * _CHUNK == len(got)

    @pytest.mark.asyncio
    async def test_stream_maps_errors_exactly_as_request_does(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "Not found"})

        c = _client_with(handler)
        with pytest.raises(FliktApiError, match="list_projects") as exc:
            await c.stream_bulk_rfis_pdf("nope", lambda _b: None)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_non_pdf_200_is_refused_not_streamed(self):
        """A proxy's HTML page on a 200 must not be saved as the RFI package."""
        got = bytearray()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>sign in</html>", headers={"content-type": "text/html"})

        c = _client_with(handler)
        with pytest.raises(FliktApiError, match="other than a PDF"):
            await c.stream_bulk_rfis_pdf("p1", got.extend)
        assert got == bytearray()

    @pytest.mark.asyncio
    async def test_stream_network_error_does_not_leak_host(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        c = _client_with(handler)
        with pytest.raises(FliktApiError) as exc:
            await c.stream_bulk_rfis_pdf("p1", lambda _b: None)
        assert "ConnectError" in str(exc.value)
        assert "api.test" not in str(exc.value)
        assert "boom" not in str(exc.value)


class TestSaveRfisPdfStreaming:
    @pytest.fixture(autouse=True)
    def _require_mcp(self):
        pytest.importorskip("mcp")

    @pytest.mark.asyncio
    async def test_local_save_writes_the_whole_body(self, monkeypatch, tmp_path):
        from flikt_mcp import server

        monkeypatch.setattr(server, "_REMOTE", False)
        monkeypatch.setattr(server.Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(server, "_get_client", lambda: _client_with(_pdf_handler(5)))
        target = tmp_path / "out" / "rfis.pdf"
        out = await server.save_rfis_pdf("p1", str(target))
        assert target.read_bytes() == b"\0" * (5 * _CHUNK)
        assert out == f"Saved RFI package ({5 * _CHUNK:,} bytes) to {target.resolve()}"

    @pytest.mark.asyncio
    async def test_a_failed_download_writes_nothing(self, monkeypatch, tmp_path):
        """The file appears only after a COMPLETE download, as before."""
        from flikt_mcp import server

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(402, json={"detail": "budget"})

        monkeypatch.setattr(server, "_REMOTE", False)
        monkeypatch.setattr(server.Path, "home", staticmethod(lambda: tmp_path))
        monkeypatch.setattr(server, "_get_client", lambda: _client_with(handler))
        target = tmp_path / "out" / "rfis.pdf"
        with pytest.raises(FliktApiError):
            await server.save_rfis_pdf("p1", str(target))
        assert not target.parent.exists()

    @pytest.mark.asyncio
    async def test_remote_reports_the_size_without_holding_the_body(self, monkeypatch):
        """Memory is independent of PDF size AND of how many tenants export at
        once: 4 concurrent 8 MiB exports peak far below ONE buffered body."""
        import asyncio
        import tracemalloc

        from flikt_mcp import server

        n_chunks = 128  # 8 MiB each
        monkeypatch.setattr(server, "_REMOTE", True)
        monkeypatch.setattr(server, "_get_client", lambda: _client_with(_pdf_handler(n_chunks)))

        tracemalloc.start()
        try:
            outs = await asyncio.gather(*(server.save_rfis_pdf("p1") for _ in range(4)))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        for out in outs:
            assert f"({n_chunks * _CHUNK:,} bytes)" in out
        assert peak < n_chunks * _CHUNK // 4, f"peak {peak:,} B: the body is being buffered"
