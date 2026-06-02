"""
Tests for the Phase 6 auto-remediation engine (src/auto_remediate.py).

Runs with pytest OR standalone:
    cd src && python tests/test_auto_remediate.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # src/ on path

import auto_remediate as ar


# ── recording deps ────────────────────────────────────────────────────────────
def make_deps(vendor="frr", blast=0, clock=None):
    calls = {"closed_loop": [], "exec": [], "gait": [], "webhook": [], "emit": []}
    t = {"v": 1_000_000.0}

    def now():
        return clock() if clock else t["v"]

    deps = ar.Deps(
        trigger_closed_loop=lambda **k: (calls["closed_loop"].append(k) or {"change_id": "chg-test"}),
        run_exec=lambda **k: (calls["exec"].append(k) or {"ok": True}),
        emit=lambda *a, **k: calls["emit"].append(a),
        gait_log=lambda e: calls["gait"].append(e),
        webhook=lambda p: calls["webhook"].append(p),
        blast_radius=lambda h: blast,
        vendor_of=lambda h: vendor,
        now=now,
    )
    return deps, calls, t


# ── pure functions ─────────────────────────────────────────────────────────────
def test_catalog_loads_eight():
    cat = ar.load_catalog()
    assert len(cat["runbooks"]) == 8
    assert "bgp_flap_reset" in cat["_by_id"]


def test_normalize_terse_vocabulary():
    n = ar.normalize_anomaly({"detector": "flap", "metric": "bgp_established", "device": "de-fra-core-01"})
    assert n["anomaly_type"] == "flap_count"
    assert n["metric"] == "bgp_session_count"
    assert n["host"] == "de-fra-core-01"


def test_match_and_vendor_filter():
    cat = ar.load_catalog()
    n = ar.normalize_anomaly({"detector": "flap", "metric": "bgp_established", "device": "x"})
    assert ar.match_runbook(n, cat, vendor="frr")["id"] == "bgp_flap_reset"
    assert ar.match_runbook(n, cat, vendor="cisco-iosxr") is None       # vendor not in runbook list
    assert ar.match_runbook(n, cat, vendor="")["id"] == "bgp_flap_reset"  # unknown vendor = lenient


def test_fill_template_missing():
    filled, missing = ar.fill_template("interface {interface}\n mtu {expected}", {"interface": "eth1"})
    assert "eth1" in filled and missing == ["expected"]
    filled2, missing2 = ar.fill_template("clear bgp * soft", {"host": "x"})
    assert missing2 == []


def test_escalate_tier():
    assert ar.escalate_tier("LOW", 0) == "LOW"
    assert ar.escalate_tier("LOW", 3) == "MEDIUM"
    assert ar.escalate_tier("LOW", 8) == "HIGH"
    assert ar.escalate_tier("HIGH", 8) == "CRIT"        # capped at CRIT


# ── engine decisions ────────────────────────────────────────────────────────────
def test_low_auto_executes_exec():
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = rem.evaluate({"detector": "flap", "metric": "bgp_established", "device": "de-fra-core-01"})
    assert rec["status"] == "auto_executed"
    assert rec["risk_tier"] == "LOW"
    assert calls["exec"][0]["command"] == "clear bgp * soft"
    assert calls["gait"][0]["verdict"] == "executed"


def test_medium_queues_no_execute():
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = rem.evaluate({"detector": "drift", "metric": "mtu", "device": "leaf1",
                        "interface": "eth2", "expected": 9000})
    assert rec["status"] == "pending_approval"
    assert rec["risk_tier"] == "MEDIUM"
    assert calls["closed_loop"] == [] and calls["exec"] == []           # NOT executed


def test_config_missing_fields_needs_enrichment():
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = rem.evaluate({"detector": "drift", "metric": "mtu", "device": "leaf1"})  # no interface/expected
    assert rec["status"] == "needs_enrichment"
    assert "interface" in rec["missing_fields"]
    assert calls["closed_loop"] == []                                   # never fire half-filled template


def test_blast_escalation_to_crit_pages_only():
    deps, calls, _ = make_deps(vendor="arista-eos", blast=8)            # mac_flap is HIGH -> CRIT
    rem = ar.AutoRemediator(deps)
    rec = rem.evaluate({"detector": "flap", "metric": "mac_move_count", "device": "leaf2",
                        "interface": "eth3"})
    assert rec["risk_tier"] == "CRIT" and rec["status"] == "paged"
    assert calls["webhook"] and calls["exec"] == [] and calls["closed_loop"] == []


def test_approve_then_execute():
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = rem.evaluate({"detector": "drift", "metric": "mtu", "device": "leaf1",
                        "interface": "eth2", "expected": 9000})
    out = rem.approve(rec["id"])
    assert out["status"] == "auto_executed"
    assert calls["closed_loop"][0]["hostname"] == "leaf1"
    assert "mtu 9000" in calls["closed_loop"][0]["proposed_change"]


def test_decline_marks_declined():
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = rem.evaluate({"detector": "drift", "metric": "mtu", "device": "leaf1",
                        "interface": "eth2", "expected": 9000})
    out = rem.decline(rec["id"], reason="maintenance window")
    assert out["status"] == "declined" and out["decline_reason"] == "maintenance window"
    assert calls["closed_loop"] == []


def test_cooldown_suppresses_repeat():
    deps, calls, t = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    a = {"detector": "flap", "metric": "bgp_established", "device": "de-fra-core-01"}
    rem.evaluate(a)
    again = rem.evaluate(a)                                             # same tick, within cooldown
    assert again.get("skipped") == "cooldown"
    assert len(calls["exec"]) == 1                                      # only fired once


def test_no_match_returns_unmatched():
    deps, _, _ = make_deps()
    rem = ar.AutoRemediator(deps)
    rec = rem.evaluate({"detector": "zscore", "metric": "totally_unknown_metric", "device": "x"})
    assert rec["matched"] is False


# ── blueprint endpoints (Flask test client) ─────────────────────────────────────
def test_endpoints():
    from flask import Flask
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    app = Flask(__name__)
    app.register_blueprint(ar.make_blueprint(rem, enable_simulate=True))
    c = app.test_client()

    assert c.get("/api/auto-remediate/status").get_json()["runbooks_loaded"] == 8
    assert len(c.get("/api/auto-remediate/runbook").get_json()["runbooks"]) == 8

    # drive one auto-fix through the simulate hook
    sim = c.post("/api/auto-remediate/simulate",
                 json={"detector": "flap", "metric": "bgp_established", "device": "de-fra-core-01"})
    assert sim.get_json()["status"] == "auto_executed"
    assert c.get("/api/auto-remediate/queue").get_json()["actions"][0]["runbook"] == "bgp_flap_reset"


def test_queue_limit_non_int_falls_back_not_500():
    """A garbled ?limit= must fall back to the default, never 500 the open GET route."""
    from flask import Flask
    deps, _, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    app = Flask(__name__)
    app.register_blueprint(ar.make_blueprint(rem, enable_simulate=False))
    c = app.test_client()
    r = c.get("/api/auto-remediate/queue?limit=notanint")
    assert r.status_code == 200
    assert isinstance(r.get_json()["actions"], list)


# ── security: injection guard + gated debug hook ────────────────────────────────
def test_rejects_injection():
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = rem.evaluate({"detector": "drift", "metric": "mtu", "device": "leaf1",
                        "interface": "eth2\n router bgp 666", "expected": 9000})
    assert rec["status"] == "rejected_invalid_field"
    assert "interface" in rec["invalid_fields"]
    assert calls["closed_loop"] == [] and calls["exec"] == []     # never executed
    assert "router bgp 666" not in rec["payload"]                 # injected content not surfaced


def test_valid_field_rules():
    assert ar.valid_field("interface", "Ethernet1/1.100")
    assert not ar.valid_field("interface", "eth0; reboot")
    assert not ar.valid_field("interface", "eth0\nfoo")
    assert ar.valid_field("peer_ip", "10.200.0.13")
    assert not ar.valid_field("peer_ip", "10.0.0.1 evil")
    assert ar.valid_field("expected", "9000")
    assert not ar.valid_field("expected", "9000 | sh")


def test_simulate_gated_off_by_default():
    from flask import Flask
    deps, _, _ = make_deps()
    rem = ar.AutoRemediator(deps)
    app = Flask(__name__)
    app.register_blueprint(ar.make_blueprint(rem, enable_simulate=False))
    c = app.test_client()
    assert c.post("/api/auto-remediate/simulate", json={}).status_code == 404   # not registered
    assert c.get("/api/auto-remediate/status").status_code == 200               # others still up


# ── Phase 6 C: decline status-guard + reason cap (audit integrity) ───────────────
def _make_pending(rem):
    """Helper: drive one MEDIUM action to pending_approval and return its record."""
    return rem.evaluate({"detector": "drift", "metric": "mtu", "device": "leaf1",
                         "interface": "eth2", "expected": 9000})


def test_decline_guard_rejects_executed_record():
    """decline() must NOT overwrite a terminal status (audit-integrity guard).

    Without the guard, declining an already auto_executed/LOW action would rewrite
    its status to 'declined', falsifying the audit trail.
    """
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = rem.evaluate({"detector": "flap", "metric": "bgp_established",
                        "device": "de-fra-core-01"})          # LOW -> auto_executed
    assert rec["status"] == "auto_executed"
    out = rem.decline(rec["id"], reason="too late")
    assert "error" in out                                     # refused
    assert "not declinable" in out["error"]
    assert rem._actions[rec["id"]]["status"] == "auto_executed"  # status untouched
    assert "decline_reason" not in rem._actions[rec["id"]]      # no audit overwrite


def test_decline_guard_rejects_already_declined():
    """Declining an already-declined record must be refused (idempotent guard)."""
    deps, _, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = _make_pending(rem)
    first = rem.decline(rec["id"], reason="window")
    assert first["status"] == "declined"
    second = rem.decline(rec["id"], reason="again")
    assert "error" in second and "not declinable" in second["error"]
    assert rem._actions[rec["id"]]["decline_reason"] == "window"   # original reason kept


def test_decline_pending_still_works():
    """Regression: declining a pending_approval record must still cancel it."""
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = _make_pending(rem)
    out = rem.decline(rec["id"], reason="maintenance window")
    assert out["status"] == "declined" and out["decline_reason"] == "maintenance window"
    assert calls["closed_loop"] == [] and calls["exec"] == []      # never executed


def test_decline_prevents_subsequent_execution():
    """A declined run can never be approved/executed afterwards."""
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = _make_pending(rem)
    rem.decline(rec["id"], reason="not now")
    out = rem.approve(rec["id"])
    assert "error" in out and "not approvable" in out["error"]
    assert calls["closed_loop"] == [] and calls["exec"] == []      # declined => never runs


def test_decline_reason_capped_and_coerced_at_route():
    """The decline route treats the body as untrusted: a non-string / over-long
    reason is coerced to str and truncated to <=500 chars before it reaches the
    record + audit log."""
    from flask import Flask
    deps, _, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = _make_pending(rem)
    app = Flask(__name__)
    app.register_blueprint(ar.make_blueprint(rem, enable_simulate=False))
    c = app.test_client()

    # over-long reason -> truncated to 500 chars
    long_reason = "x" * 2000
    out = c.post(f"/api/auto-remediate/decline/{rec['id']}", json={"reason": long_reason}).get_json()
    assert out["status"] == "declined"
    assert len(out["decline_reason"]) == 500


def test_decline_reason_non_string_coerced_at_route():
    """A non-string reason (e.g. a number) is coerced to str, never passed raw."""
    from flask import Flask
    deps, _, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = _make_pending(rem)
    app = Flask(__name__)
    app.register_blueprint(ar.make_blueprint(rem, enable_simulate=False))
    c = app.test_client()
    out = c.post(f"/api/auto-remediate/decline/{rec['id']}", json={"reason": 12345}).get_json()
    assert out["status"] == "declined"
    assert out["decline_reason"] == "12345"                # coerced, not the int


# ── Phase 6 C: approve/decline behind the X-API-Key gate ─────────────────────────
def _gated_app(rem):
    """Build a Flask app whose before_request mirrors the real app.py X-API-Key
    gate (MVLAB_API_KEY, fail-closed 503, hmac.compare_digest), wrapping the
    auto-remediate blueprint exactly as the production app does."""
    import hmac
    from flask import Flask, jsonify, request
    app = Flask(__name__)
    key = os.environ.get("MVLAB_API_KEY", "")

    @app.before_request
    def _gate():
        if request.method.upper() in ("GET", "HEAD", "OPTIONS"):
            return None                                    # reads stay open
        if not key:
            return jsonify({"error": "server not configured"}), 503
        supplied = request.headers.get("X-API-Key", "")
        if not supplied or not hmac.compare_digest(supplied, key):
            return jsonify({"error": "unauthorized"}), 403
        return None

    app.register_blueprint(ar.make_blueprint(rem, enable_simulate=False))
    return app


def test_approve_gate_rejects_missing_key(monkeypatch):
    monkeypatch.setenv("MVLAB_API_KEY", "s3cret")
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = _make_pending(rem)
    c = _gated_app(rem).test_client()
    # no X-API-Key header -> 403, and the action is NOT executed
    resp = c.post(f"/api/auto-remediate/approve/{rec['id']}")
    assert resp.status_code == 403
    assert calls["closed_loop"] == [] and calls["exec"] == []
    assert rem._actions[rec["id"]]["status"] == "pending_approval"   # unchanged


def test_decline_gate_rejects_missing_key(monkeypatch):
    monkeypatch.setenv("MVLAB_API_KEY", "s3cret")
    deps, _, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = _make_pending(rem)
    c = _gated_app(rem).test_client()
    resp = c.post(f"/api/auto-remediate/decline/{rec['id']}", json={"reason": "x"})
    assert resp.status_code == 403
    assert rem._actions[rec["id"]]["status"] == "pending_approval"   # not declined


def test_approve_gate_allows_valid_key(monkeypatch):
    monkeypatch.setenv("MVLAB_API_KEY", "s3cret")
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = _make_pending(rem)
    c = _gated_app(rem).test_client()
    resp = c.post(f"/api/auto-remediate/approve/{rec['id']}", headers={"X-API-Key": "s3cret"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "auto_executed"
    assert calls["closed_loop"][0]["hostname"] == "leaf1"            # executed via engine path


def test_decline_gate_allows_valid_key(monkeypatch):
    monkeypatch.setenv("MVLAB_API_KEY", "s3cret")
    deps, calls, _ = make_deps(vendor="frr")
    rem = ar.AutoRemediator(deps)
    rec = _make_pending(rem)
    c = _gated_app(rem).test_client()
    resp = c.post(f"/api/auto-remediate/decline/{rec['id']}",
                  json={"reason": "scheduled change freeze"},
                  headers={"X-API-Key": "s3cret"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "declined" and body["decline_reason"] == "scheduled change freeze"
    assert calls["closed_loop"] == [] and calls["exec"] == []        # decline never executes


# ── standalone runner (no pytest needed) ─────────────────────────────────────────
if __name__ == "__main__":
    import inspect

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = skipped = 0
    for fn in fns:
        # Tests that take pytest fixtures (e.g. monkeypatch) can't run standalone.
        if inspect.signature(fn).parameters:
            print(f"  SKIP  {fn.__name__} (needs pytest fixtures)")
            skipped += 1
            continue
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    total = len(fns) - skipped
    print(f"\n{passed}/{total} passed ({skipped} skipped)")
    sys.exit(0 if passed == total else 1)
