"""
Phase 6 — Event-Initiated Remediation engine.

Closes the last gap in the AI-SRE loop: a detected anomaly auto-triggers the
existing closed-loop change pipeline, risk-gated, with no human button-press for
LOW-risk events and one-click approval for MEDIUM/HIGH.

    anomaly --match(auto.yaml)--> runbook --risk_tier--> {
        LOW          : execute now
        MEDIUM/HIGH  : queue for one-click approval
        CRIT         : webhook page-out only, never auto-acts
    } --execute--> closed-loop pipeline (config) | exec | collect

Design notes
------------
* Pure logic (load/normalize/match/fill/tier/decide) is import-safe and has NO
  Flask, network, or InfluxDB dependency, so it unit-tests in isolation.
* All side effects (trigger closed-loop, run exec, emit to agent log, GAIT,
  webhook, blast-radius, anomaly polling, host->vendor) are injected via `Deps`,
  defaulting to no-ops. `make_app_deps()` wires the real implementations.
* `risk_tier` from the catalog is a FLOOR; blast-radius can ESCALATE, never demote.
"""
from __future__ import annotations

import os
import re
import time
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

import yaml

# ── vocabulary normalization: real /api/anomaly/detect emits terse names ──────
# detector: "flap" | "zscore"   metric: "bgp_established" | "interface_up"
_DETECTOR_ALIAS = {"flap": "flap_count", "zscore": "zscore", "drift": "drift"}
_METRIC_ALIAS = {"bgp_established": "bgp_session_count", "interface_up": "admin_state"}

TIER_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRIT": 3}
_TIER_BY_ORDER = {v: k for k, v in TIER_ORDER.items()}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

DEFAULT_CATALOG = os.path.join(os.path.dirname(__file__), "runbooks", "auto.yaml")


# ── pure functions ────────────────────────────────────────────────────────────
def load_catalog(path: str = DEFAULT_CATALOG) -> dict:
    """Load + index the runbook catalog. Raises on malformed YAML/schema."""
    doc = yaml.safe_load(open(path))
    runbooks = doc.get("runbooks") or []
    if not runbooks:
        raise ValueError("auto.yaml has no runbooks")
    seen = set()
    for rb in runbooks:
        rid = rb.get("id")
        if not rid or rid in seen:
            raise ValueError(f"runbook id missing or duplicate: {rid!r}")
        seen.add(rid)
        if rb.get("risk_tier") not in TIER_ORDER:
            raise ValueError(f"{rid}: bad risk_tier {rb.get('risk_tier')!r}")
        if (rb.get("action") or {}).get("type") not in ("config", "exec", "collect"):
            raise ValueError(f"{rid}: bad action.type")
    doc["_by_id"] = {rb["id"]: rb for rb in runbooks}
    doc.setdefault("defaults", {})
    return doc


def normalize_anomaly(a: dict) -> dict:
    """Map a raw /api/anomaly/detect finding onto the catalog vocabulary."""
    detector = str(a.get("detector") or a.get("anomaly_type") or "").lower()
    metric = str(a.get("metric") or "").lower()
    return {
        "anomaly_type": _DETECTOR_ALIAS.get(detector, detector),
        "metric": _METRIC_ALIAS.get(metric, metric),
        "host": a.get("device") or a.get("host") or a.get("hostname") or "",
        "severity": (a.get("severity") or "warning").lower(),
        "raw": a,
    }


def match_runbook(norm: dict, catalog: dict, vendor: str = "") -> Optional[dict]:
    """First runbook whose match block fits (anomaly_type + metric [+ vendor])."""
    for rb in catalog.get("runbooks", []):
        m = rb.get("match", {})
        if m.get("anomaly_type") != norm["anomaly_type"]:
            continue
        if m.get("metric") != norm["metric"]:
            continue
        vendors = m.get("vendor")
        if vendor and vendors and vendor not in vendors:
            continue
        return rb
    return None


def fill_template(text: str, ctx: dict) -> tuple[str, list[str]]:
    """Substitute {placeholders} from ctx. Returns (filled, missing_keys)."""
    missing: list[str] = []

    def repl(mo: re.Match) -> str:
        key = mo.group(1)
        val = ctx.get(key)
        if val in (None, ""):
            missing.append(key)
            return mo.group(0)
        return str(val)

    return _PLACEHOLDER.sub(repl, text or ""), missing


def escalate_tier(floor: str, blast_count: int) -> str:
    """Blast-radius escalation: never below the runbook floor."""
    bump = 1 if blast_count >= 3 else 0
    bump = 2 if blast_count >= 8 else bump
    order = min(TIER_ORDER[floor] + bump, TIER_ORDER["CRIT"])
    return _TIER_BY_ORDER[order]


# ── dependency surface (all side effects) ─────────────────────────────────────
@dataclass
class Deps:
    trigger_closed_loop: Callable[..., dict] = lambda **k: {"stub": True}        # (hostname, proposed_change, timeout_s)
    run_exec: Callable[..., dict] = lambda **k: {"stub": True}                   # (host, command)
    emit: Callable[..., None] = lambda *a, **k: None                            # (stream_id, step, detail, level)
    gait_log: Callable[[dict], None] = lambda e: None
    webhook: Callable[[dict], None] = lambda p: None
    blast_radius: Callable[[str], int] = lambda h: 0
    fetch_anomalies: Callable[[], list] = list
    fetch_forecast: Callable[[], list] = list
    vendor_of: Callable[[str], str] = lambda h: ""
    now: Callable[[], float] = time.time


# ── the engine ────────────────────────────────────────────────────────────────
class AutoRemediator:
    def __init__(self, deps: Optional[Deps] = None, catalog_path: str = DEFAULT_CATALOG):
        self.deps = deps or Deps()
        self.catalog = load_catalog(catalog_path)
        self.cooldown_min = int(self.catalog["defaults"].get("cooldown_min", 15))
        self.default_timeout = int(self.catalog["defaults"].get("timeout_s", 30))
        self._lock = threading.Lock()
        self._actions: dict[str, dict] = {}     # id -> action record
        self._order: list[str] = []             # insertion order (newest last)
        self._cooldown: dict[tuple, float] = {}  # (host, runbook_id) -> ts
        self._ticks = 0
        self._last_tick_ts: Optional[float] = None

    # -- core decision for a single anomaly --------------------------------------
    def evaluate(self, anomaly: dict) -> dict:
        d = self.deps
        norm = normalize_anomaly(anomaly)
        host = norm["host"]
        vendor = d.vendor_of(host) if host else ""
        rb = match_runbook(norm, self.catalog, vendor)
        if not rb:
            return {"matched": False, "host": host, "anomaly": norm}

        # cooldown — don't thrash the same (host, runbook)
        key = (host, rb["id"])
        last = self._cooldown.get(key, 0)
        if d.now() - last < self.cooldown_min * 60:
            return {"matched": True, "skipped": "cooldown", "runbook": rb["id"], "host": host}

        ctx = {
            "host": host, "vendor": vendor, "severity": norm["severity"],
            "metric": norm["metric"], **{k: v for k, v in norm["raw"].items()
                                         if isinstance(v, (str, int, float))},
        }
        action = rb["action"]
        atype = action["type"]
        payload = action.get("proposed_change") if atype == "config" else action.get("command")
        filled, missing = fill_template(payload, ctx)
        tier = escalate_tier(rb["risk_tier"], d.blast_radius(host) if host else 0)

        rec = {
            "id": f"ar-{uuid.uuid4().hex[:10]}",
            "ts": d.now(), "host": host, "vendor": vendor,
            "runbook": rb["id"], "anomaly_type": norm["anomaly_type"],
            "metric": norm["metric"], "severity": norm["severity"],
            "risk_tier": tier, "action_type": atype, "payload": filled,
            "missing_fields": missing, "status": "pending", "result": None,
        }

        if missing:
            rec["status"] = "needs_enrichment"            # safe: never fire a half-filled template
        elif tier == "CRIT":
            rec["status"] = "paged"
            d.gait_log({"actor": "auto-remediate", "verdict": "paged",
                        "runbook": rb["id"], "host": host, "tier": tier})
            d.webhook({"event": "crit_anomaly", **rec})
        elif tier == "LOW":
            rec["status"], rec["result"] = self._execute(rec)
        else:                                              # MEDIUM / HIGH
            rec["status"] = "pending_approval"

        self._cooldown[key] = d.now()
        self._record(rec)
        d.emit("auto-remediate",
               f"🤖 {rb['id']} [{tier}] {rec['status']}",
               f"{host} · {norm['anomaly_type']}/{norm['metric']}",
               "warn" if tier in ("HIGH", "CRIT") else "info")
        return rec

    def _execute(self, rec: dict) -> tuple[str, dict]:
        d = self.deps
        try:
            if rec["action_type"] == "config":
                res = d.trigger_closed_loop(hostname=rec["host"],
                                            proposed_change=rec["payload"],
                                            timeout_s=self.default_timeout)
            else:                                          # exec | collect
                res = d.run_exec(host=rec["host"], command=rec["payload"])
            d.gait_log({"actor": "auto-remediate", "verdict": "executed",
                        "runbook": rec["runbook"], "host": rec["host"], "tier": rec["risk_tier"]})
            return "auto_executed", res
        except Exception as exc:                           # noqa: BLE001 — surface, never crash the loop
            return "execute_failed", {"error": str(exc)}

    # -- approval workflow -------------------------------------------------------
    def approve(self, action_id: str, actor: str = "mcp-approved") -> dict:
        with self._lock:
            rec = self._actions.get(action_id)
        if not rec:
            return {"error": "action_id not found"}
        if rec["status"] not in ("pending_approval", "needs_enrichment"):
            return {"error": f"not approvable (status={rec['status']})"}
        if rec["missing_fields"]:
            return {"error": "cannot execute — unresolved fields", "missing": rec["missing_fields"]}
        rec["status"], rec["result"] = self._execute(rec)
        rec["approved_by"] = actor
        self.deps.gait_log({"actor": actor, "verdict": "approved",
                            "runbook": rec["runbook"], "host": rec["host"]})
        return rec

    def decline(self, action_id: str, reason: str = "", actor: str = "operator") -> dict:
        with self._lock:
            rec = self._actions.get(action_id)
        if not rec:
            return {"error": "action_id not found"}
        rec["status"] = "declined"
        rec["decline_reason"] = reason
        self.deps.gait_log({"actor": actor, "verdict": "declined",
                            "runbook": rec["runbook"], "host": rec["host"], "reason": reason})
        return rec

    # -- background poll ---------------------------------------------------------
    def tick(self) -> dict:
        found = []
        for a in (self.deps.fetch_anomalies() or []):
            rec = self.evaluate(a)
            if rec.get("id"):
                found.append(rec["id"])
        self._ticks += 1
        self._last_tick_ts = self.deps.now()
        return {"tick": self._ticks, "evaluated": len(found), "new_action_ids": found}

    # -- read surface ------------------------------------------------------------
    def _record(self, rec: dict) -> None:
        with self._lock:
            self._actions[rec["id"]] = rec
            self._order.append(rec["id"])

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return [self._actions[i] for i in self._order[-limit:][::-1]]

    def status(self) -> dict:
        with self._lock:
            by_status: dict[str, int] = {}
            for r in self._actions.values():
                by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        return {
            "ticks": self._ticks, "last_tick_ts": self._last_tick_ts,
            "runbooks_loaded": len(self.catalog["runbooks"]),
            "total_actions": len(self._order), "by_status": by_status,
            "cooldown_min": self.cooldown_min,
        }

    def runbook_summary(self) -> list[dict]:
        return [{"id": rb["id"], "match": rb["match"], "risk_tier": rb["risk_tier"],
                 "action_type": rb["action"]["type"], "description": rb.get("description", "")}
                for rb in self.catalog["runbooks"]]


# ── loop + Flask blueprint (item A + D) ───────────────────────────────────────
def start_loop(remediator: AutoRemediator, interval_s: int) -> threading.Thread:
    def _run() -> None:
        while True:
            try:
                remediator.tick()
            except Exception:  # noqa: BLE001 — never let the loop die
                pass
            time.sleep(max(15, interval_s))
    t = threading.Thread(target=_run, daemon=True, name="auto-remediate")
    t.start()
    return t


def make_blueprint(remediator: AutoRemediator):
    from flask import Blueprint, jsonify, request
    bp = Blueprint("auto_remediate", __name__)

    @bp.route("/api/auto-remediate/status", methods=["GET"])
    def _status():
        return jsonify(remediator.status())

    @bp.route("/api/auto-remediate/queue", methods=["GET"])
    def _queue():
        return jsonify({"actions": remediator.recent(int(request.args.get("limit", 50)))})

    @bp.route("/api/auto-remediate/runbook", methods=["GET"])
    def _runbooks():
        return jsonify({"runbooks": remediator.runbook_summary()})

    @bp.route("/api/auto-remediate/approve/<action_id>", methods=["POST"])
    def _approve(action_id):
        return jsonify(remediator.approve(action_id))

    @bp.route("/api/auto-remediate/decline/<action_id>", methods=["POST"])
    def _decline(action_id):
        body = request.get_json(silent=True) or {}
        return jsonify(remediator.decline(action_id, body.get("reason", "")))

    # test-only hook: inject a synthetic anomaly to drive a demo without waiting
    @bp.route("/api/auto-remediate/simulate", methods=["POST"])
    def _simulate():
        body = request.get_json(force=True) or {}
        return jsonify(remediator.evaluate(body))

    return bp
