"""Flask test-client tests for the AEGIS preflight blueprints.

Registers the blueprints in a minimal Flask app (no full app.py dependency tree) and
exercises the real HTTP surface:

  POST /api/preflight/twin/{spawn,apply,destroy}   (mocked containerlab runner)
  POST /api/preflight/run                          (SimulatorBackend, sim mode)

pytest-compatible (test_* funcs) AND runnable standalone: `python3 src/tests/test_preflight_flask.py`.
"""
from __future__ import annotations
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AEGIS_CLAB_MODE", "binary")  # deterministic: test the host-binary path

from flask import Flask
from preflight_twin import TwinManager, make_blueprint as twin_bp
from preflight_run import make_blueprint as run_bp


class _MockRunner:
    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, cmd, timeout=300.0, stdin=None):
        with self._lock:
            self.calls.append(list(cmd))
        if cmd[:2] == ["docker", "ps"]:
            flt = next((a for a in cmd if a.startswith("name=clab-")), "")
            tid = flt[len("name=clab-"):].rstrip("-")
            return 0, f"clab-{tid}-spine1\nclab-{tid}-leaf1\n", ""
        return 0, "ok", ""


def _client():
    app = Flask(__name__)
    app.register_blueprint(twin_bp(TwinManager(runner=_MockRunner(),
                                                workdir="/tmp/aegis-flask")))
    app.register_blueprint(run_bp())
    return app.test_client()


# ── twin endpoints ───────────────────────────────────────────────────────
def test_twin_spawn_apply_destroy_happy_path():
    c = _client()
    r = c.post("/api/preflight/twin/spawn", json={"lab": "clos-evpn"})
    assert r.status_code == 200, r.get_json()
    tid = r.get_json()["twin_id"]
    assert tid.startswith("twin-")

    r = c.post("/api/preflight/twin/apply", json={
        "twin_id": tid,
        "configs": [{"device": "leaf1", "vendor": "frr", "config": "router bgp 65010"}]})
    assert r.status_code == 200
    assert r.get_json()["applied"] is True

    r = c.post("/api/preflight/twin/destroy", json={"twin_id": tid})
    assert r.status_code == 200
    assert r.get_json()["destroyed"] is True


def test_twin_spawn_rejects_unknown_lab():
    c = _client()
    r = c.post("/api/preflight/twin/spawn", json={"lab": "../etc/passwd"})
    assert r.status_code == 400
    assert "unknown lab" in r.get_json()["error"]


def test_twin_destroy_refuses_non_twin_id():
    c = _client()
    r = c.post("/api/preflight/twin/destroy", json={"twin_id": "clab-clos-evpn-spine1"})
    assert r.status_code == 403


def test_twin_apply_blocks_injection_device_name():
    c = _client()
    tid = c.post("/api/preflight/twin/spawn", json={"lab": "minimal"}).get_json()["twin_id"]
    r = c.post("/api/preflight/twin/apply", json={
        "twin_id": tid,
        "configs": [{"device": "leaf1; rm -rf /", "vendor": "frr", "config": "x"}]})
    body = r.get_json()
    assert body["applied"] is False
    assert body["per_device"][0]["error"] == "invalid device name"


# ── preflight run endpoint ─────────────────────────────────────────────────
def test_run_sim_returns_sealed_bundle():
    c = _client()
    r = c.post("/api/preflight/run", json={
        "intent": "add vlan 40 to leaf-2 and peer bgp", "lab": "clos-evpn",
        "frameworks": ["pci_dss_v4", "nist_800-53"]})
    assert r.status_code == 200, r.get_json()
    b = r.get_json()
    assert b["bundle_version"] == "1.0"
    assert b["integrity"]["egress"] == "none"
    assert b["verdict"]["decision"] in ("ship_ready", "blocked", "needs_approval")
    assert len(b["integrity"]["sha256"]) == 64


def test_run_blocks_plaintext_key_with_pci_fail():
    c = _client()
    r = c.post("/api/preflight/run", json={
        "intent": "add vlan 40 with plaintext bgp authentication-key abc123",
        "lab": "clos-evpn", "frameworks": ["pci_dss_v4"]})
    b = r.get_json()
    assert b["verdict"]["decision"] == "blocked"
    pci = {(x["control"], x["status"]) for x in b["validation"]["compliance"]}
    assert ("8.3.1", "fail") in pci


def test_run_rejects_empty_intent():
    c = _client()
    r = c.post("/api/preflight/run", json={"intent": "   ", "lab": "clos-evpn"})
    assert r.status_code == 400
    assert r.get_json()["stage"] == "guard"


# ── config-import path (no LLM in the loop) ────────────────────────────────
def test_config_import_ships_clean_config():
    c = _client()
    r = c.post("/api/preflight/run", json={
        "source": "config_import", "lab": "minimal", "vendor": "frr",
        "config": "router ospf 1\n network 10.0.0.0 area 0"})
    assert r.status_code == 200
    b = r.get_json()
    assert b["change"]["source"] == "config_import"
    assert b["change"]["generated_configs"][0]["grounded_commands"] == ["operator-supplied"]


def test_config_import_blocks_plaintext_key():
    c = _client()
    r = c.post("/api/preflight/run", json={
        "source": "config_import", "lab": "clos-evpn", "vendor": "frr",
        "config": "neighbor 10.0.0.1 remote-as 65020\n set bgp authentication-key plaintext abc123",
        "frameworks": ["pci_dss_v4"]})
    b = r.get_json()
    assert b["verdict"]["decision"] == "blocked"
    assert ("8.3.1", "fail") in {(x["control"], x["status"]) for x in b["validation"]["compliance"]}


def test_config_import_requires_config():
    c = _client()
    r = c.post("/api/preflight/run", json={"source": "config_import", "lab": "minimal"})
    assert r.status_code == 400
    assert r.get_json()["stage"] == "guard"


# ── standalone runner (no pytest needed) ───────────────────────────────────
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {e!r}")
    print(f"\n=== FLASK TESTS: {'PASS' if not failed else 'FAIL'} "
          f"({len(tests)-failed}/{len(tests)}) ===")
    sys.exit(1 if failed else 0)
