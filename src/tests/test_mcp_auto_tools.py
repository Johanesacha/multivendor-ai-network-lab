"""
Tests for the Phase 6E auto-remediation MCP tools (src/mcp_dcn_server.py).

Hermetic — the network helpers (_get/_post) are monkeypatched, so no live
:5757 API or fabric is contacted. Verifies the 5 mv_auto_* tools exist and
call the right endpoints with the right params, matching sibling conventions:
GET tools return the RAW _get string; POST tools return json.dumps(_post(...)).

Runs with pytest OR standalone:
    cd src && python tests/test_mcp_auto_tools.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src/ on path

try:
    import mcp.server.fastmcp  # noqa: F401  — the FastMCP dependency the server needs
    _HAS_MCP = True
except Exception:  # pragma: no cover - environment-dependent
    _HAS_MCP = False


def _load_server(monkeypatch_obj):
    """Import the MCP server with _get/_post replaced by recorders."""
    import mcp_dcn_server as srv

    calls = {"get": [], "post": []}

    def fake_get(path, params=None, timeout=30):
        calls["get"].append((path, params))
        return json.dumps({"ok": True, "path": path, "params": params})

    def fake_post(path, body, timeout=180):
        calls["post"].append((path, body))
        return {"ok": True, "path": path, "body": body}

    monkeypatch_obj.setattr(srv, "_get", fake_get)
    monkeypatch_obj.setattr(srv, "_post", fake_post)
    return srv, calls


# ── pytest entry points ─────────────────────────────────────────────────────────
def _pytest_guard():
    if not _HAS_MCP:
        import pytest
        pytest.skip("mcp.server.fastmcp not installed in this interpreter", allow_module_level=False)


def test_five_auto_tools_present(monkeypatch):
    _pytest_guard()
    srv, _ = _load_server(monkeypatch)
    for name in ("mv_auto_status", "mv_auto_queue", "mv_auto_runbooks",
                 "mv_auto_approve", "mv_auto_decline"):
        assert hasattr(srv, name), f"missing MCP tool {name}"
        assert callable(getattr(srv, name))


def test_status_calls_status_endpoint(monkeypatch):
    _pytest_guard()
    srv, calls = _load_server(monkeypatch)
    out = srv.mv_auto_status()
    assert calls["get"] == [("/api/auto-remediate/status", None)]
    assert isinstance(out, str)  # GET tools return the raw _get string


def test_queue_passes_limit_as_str(monkeypatch):
    _pytest_guard()
    srv, calls = _load_server(monkeypatch)
    srv.mv_auto_queue(limit=5)
    assert calls["get"] == [("/api/auto-remediate/queue", {"limit": "5"})]


def test_runbooks_uses_singular_path(monkeypatch):
    _pytest_guard()
    srv, calls = _load_server(monkeypatch)
    srv.mv_auto_runbooks()
    assert calls["get"] == [("/api/auto-remediate/runbook", None)]  # singular


def test_approve_posts_empty_body_and_json_dumps(monkeypatch):
    _pytest_guard()
    srv, calls = _load_server(monkeypatch)
    out = srv.mv_auto_approve("ar-0123456789")
    assert calls["post"] == [("/api/auto-remediate/approve/ar-0123456789", {})]
    # POST tools return json.dumps(_post(...)) — must be a JSON string, not a dict
    assert isinstance(out, str)
    assert json.loads(out)["ok"] is True


def test_decline_posts_reason_and_json_dumps(monkeypatch):
    _pytest_guard()
    srv, calls = _load_server(monkeypatch)
    out = srv.mv_auto_decline("ar-0123456789", reason="not safe right now")
    assert calls["post"] == [
        ("/api/auto-remediate/decline/ar-0123456789", {"reason": "not safe right now"})
    ]
    assert isinstance(out, str)
    assert json.loads(out)["ok"] is True


def test_decline_default_reason_is_empty(monkeypatch):
    _pytest_guard()
    srv, calls = _load_server(monkeypatch)
    srv.mv_auto_decline("ar-abcdef0123")
    assert calls["post"] == [("/api/auto-remediate/decline/ar-abcdef0123", {"reason": ""})]


# ── standalone runner (no pytest needed) ─────────────────────────────────────────
class _MiniMonkeyPatch:
    """Minimal setattr-only monkeypatch shim for the standalone runner so the
    file runs under an interpreter that has `mcp` but not pytest."""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value):
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self):
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        self._undo.clear()


if __name__ == "__main__":
    if not _HAS_MCP:
        print("SKIP — mcp.server.fastmcp not installed in this interpreter")
        sys.exit(0)

    fns = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    passed = 0
    for fn in fns:
        mp = _MiniMonkeyPatch()
        try:
            fn(mp)
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
        finally:
            mp.undo()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
