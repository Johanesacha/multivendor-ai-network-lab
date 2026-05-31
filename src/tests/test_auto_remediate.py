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
    app.register_blueprint(ar.make_blueprint(rem))
    c = app.test_client()

    assert c.get("/api/auto-remediate/status").get_json()["runbooks_loaded"] == 8
    assert len(c.get("/api/auto-remediate/runbook").get_json()["runbooks"]) == 8

    # drive one auto-fix through the simulate hook
    sim = c.post("/api/auto-remediate/simulate",
                 json={"detector": "flap", "metric": "bgp_established", "device": "de-fra-core-01"})
    assert sim.get_json()["status"] == "auto_executed"
    assert c.get("/api/auto-remediate/queue").get_json()["actions"][0]["runbook"] == "bgp_flap_reset"


# ── standalone runner (no pytest needed) ─────────────────────────────────────────
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
