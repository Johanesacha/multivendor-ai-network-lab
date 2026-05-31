"""
test_mcp_dcn_get.py — the stdlib MCP client must URL-encode query-param values.

Ultrareview #4 (HIGH): _get() builds the query string from caller/LLM-supplied
tool arguments. Without percent-encoding, a value like "DE-FRA&admin=1" splices a
second query parameter into the request. urlencode() neutralizes that.

Skipped automatically if the MCP SDK isn't installed in the running interpreter.
"""

import importlib

import pytest

pytest.importorskip("mcp.server.fastmcp")  # MCP SDK required to import the server
mcp_dcn_server = importlib.import_module("mcp_dcn_server")


class _FakeResp:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _capture_url(monkeypatch) -> dict:
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp("ok")

    monkeypatch.setattr(mcp_dcn_server.urllib.request, "urlopen", fake_urlopen)
    return captured


def test_get_url_encodes_param_values(monkeypatch):
    captured = _capture_url(monkeypatch)
    mcp_dcn_server._get("/api/devices", {"site": "DE-FRA&admin=1"})
    # the injected '&admin=1' must be percent-encoded, never spliced as a 2nd param
    assert "admin=1" not in captured["url"]
    assert "DE-FRA%26admin%3D1" in captured["url"]


def test_get_drops_empty_params_but_keeps_truthy(monkeypatch):
    captured = _capture_url(monkeypatch)
    mcp_dcn_server._get("/api/devices", {"site": "", "role": "core"})
    assert "site=" not in captured["url"]
    assert "role=core" in captured["url"]
