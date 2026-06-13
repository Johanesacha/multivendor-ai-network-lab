"""
Integration tests for the auto-remediation -> /api/run exec path (P1 #8).

These drive the REAL Flask `/api/run` route (via the Flask test client) — NOT a
mock of `is_command_blocked` — wired into the REAL `AutoRemediator` engine, so they
exercise the full path:

    AutoRemediator._execute -> run_exec -> POST /api/run
        -> is_command_blocked() guard -> run_command_on_device() (device transport)

The single device-transport call (`run_command_on_device`) is the only thing mocked,
mirroring the existing `mock_ssh` fixture. Everything else is the production code.

Regression target — the audit-trail integrity bug:
  A LOW-tier `clear ...` runbook was silently blocked by the read-only `/api/run`
  guard (403), but `_ar_run_exec` swallowed the 403 into an error dict instead of
  raising, so `_execute` still wrote a GAIT record with verdict "executed". The
  immutable audit log claimed a command ran that never did.

We assert BOTH halves of the fix:
  (a) the sanctioned `runbook_exec` path lets the LOW `clear` runbook actually run,
      and the audit record is "executed";
  (b) if the sanctioned path is removed/blocked, the run is recorded as a FAILURE
      ("failed" / status != auto_executed) — NEVER a false "executed".
"""
from __future__ import annotations

import os
import sys

import pytest

# src/ on path (mirrors conftest / sibling tests)
TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL_DIR)

import auto_remediate as ar  # noqa: E402

# A LOW-tier anomaly that matches runbook #1 (bgp_flap_reset -> "clear bgp * soft").
_LOW_CLEAR_ANOMALY = {
    "detector": "flap",
    "metric": "bgp_established",
    "device": "de-fra-core-01",
}
_API_KEY = "test-key-123"


def _wire_remediator(app_module, test_client, *, runbook_exec: bool):
    """Build a real AutoRemediator whose run_exec drives the REAL /api/run route via
    the Flask test client (not a network socket). `runbook_exec` toggles the
    sanctioned-exec flag so we can test both the working path and the honest-failure
    path against the same real guard.

    Returns (remediator, gait_records).
    """
    gait_records: list[dict] = []

    def run_exec(host, command):
        # Mirrors app.py `_ar_run_exec`, but POSTs through the in-process test client
        # so the REAL /api/run route (real is_command_blocked + real API-key gate) runs.
        body = {"hostname": host, "raw": command}
        if runbook_exec:
            body["runbook_exec"] = True
        resp = test_client.post("/api/run", json=body,
                                headers={"X-API-Key": _API_KEY})
        if resp.status_code < 200 or resp.status_code >= 300:
            return {"ok": False, "status": resp.status_code,
                    "error": resp.get_data(as_text=True)[:200]}
        data = resp.get_json()
        if isinstance(data, dict) and data.get("success") is False:
            return {"ok": False, "status": resp.status_code, **data}
        return data

    deps = ar.Deps(
        run_exec=run_exec,
        gait_log=lambda e: gait_records.append(e),
        vendor_of=lambda h: "frr",
        blast_radius=lambda h: 0,
    )
    return ar.AutoRemediator(deps), gait_records


@pytest.fixture()
def real_run_app(monkeypatch):
    """Real app module with the API-key gate ENABLED and device transport mocked."""
    import app as app_module
    from unittest.mock import MagicMock

    # Enable the X-API-Key gate (module-level global is read by _require_api_key).
    monkeypatch.setattr(app_module, "MVLAB_API_KEY", _API_KEY)
    # Mock ONLY the device transport — not is_command_blocked, not the route.
    sent = MagicMock(return_value={"success": True, "output": "cleared",
                                   "command": "clear bgp * soft"})
    monkeypatch.setattr(app_module, "run_command_on_device", sent)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield app_module, client, sent


# ── (a) sanctioned path: the LOW clear runbook actually executes ─────────────────
def test_low_clear_runbook_executes_via_sanctioned_path(real_run_app):
    app_module, client, sent = real_run_app
    rem, gait = _wire_remediator(app_module, client, runbook_exec=True)

    rec = rem.evaluate(_LOW_CLEAR_ANOMALY)

    assert rec["risk_tier"] == "LOW"
    assert rec["payload"] == "clear bgp * soft"
    # The real device transport WAS invoked (command reached the device layer).
    sent.assert_called_once()
    assert "clear bgp * soft" in str(sent.call_args)
    # Status is executed AND the audit verdict is "executed" — truthfully this time.
    assert rec["status"] == "auto_executed"
    assert gait and gait[-1]["verdict"] == "executed"


# ── (b) honest failure: a blocked clear is NEVER logged as "executed" ────────────
def test_blocked_clear_runbook_records_failed_not_executed(real_run_app):
    """Without the sanctioned flag the read-only guard returns 403. The audit record
    MUST be a failure — the regression bug logged it as 'executed'."""
    app_module, client, sent = real_run_app
    rem, gait = _wire_remediator(app_module, client, runbook_exec=False)

    rec = rem.evaluate(_LOW_CLEAR_ANOMALY)

    # The /api/run guard blocked it -> device transport never ran.
    sent.assert_not_called()
    # CRITICAL: the action is recorded as a failure, never a false "executed".
    assert rec["status"] != "auto_executed"
    assert rec["status"] == "execute_failed"
    assert gait, "an audit record must still be written"
    assert gait[-1]["verdict"] == "failed"
    assert all(g["verdict"] != "executed" for g in gait), \
        "blocked exec must NEVER produce an 'executed' audit verdict"


# ── route-level guard assertions (real is_command_blocked) ───────────────────────
def test_api_run_blocks_clear_for_normal_users(real_run_app):
    """Normal interactive callers (no runbook_exec flag) still get 403 on clear."""
    _app, client, _sent = real_run_app
    resp = client.post("/api/run",
                       json={"hostname": "de-fra-core-01", "raw": "clear bgp * soft"},
                       headers={"X-API-Key": _API_KEY})
    assert resp.status_code == 403
    assert "BLOCKED" in resp.get_json()["error"]


def test_api_run_runbook_exec_only_allows_operational_clears(real_run_app):
    """The sanctioned flag permits allowlisted operational clears but NOT config
    writes — a `configure`/`set` style command stays blocked even with the flag."""
    _app, client, _sent = real_run_app
    # allowlisted operational clear -> permitted (200)
    ok = client.post("/api/run",
                     json={"hostname": "de-fra-core-01", "raw": "clear counters eth1",
                           "runbook_exec": True},
                     headers={"X-API-Key": _API_KEY})
    assert ok.status_code == 200
    # a write/config command is still blocked even WITH the runbook_exec flag.
    blocked = client.post("/api/run",
                          json={"hostname": "de-fra-core-01",
                                "raw": "configure terminal", "runbook_exec": True},
                          headers={"X-API-Key": _API_KEY})
    assert blocked.status_code == 403


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
