"""Stress + invariant tests for preflight_twin.TwinManager.

Runs with NO Docker and NO Flask: a MockRunner records every command the manager would
execute and simulates clab/docker output. We then assert the safety invariants hold across
thousands of randomized + adversarial calls, AND that no command ever targets a production
container/topology.

Run:  python3 src/tests/test_preflight_twin.py [N]
"""
from __future__ import annotations
import json
import os
import random
import re
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AEGIS_CLAB_MODE", "binary")  # deterministic: test the host-binary path
from preflight_twin import TwinManager, TwinError, LAB_TOPOLOGIES, MAX_TWINS  # noqa: E402

# Tokens that must NEVER reach a command argument (shell-injection guard, ISO-3/6).
INJECTION = [";rm -rf /", "$(reboot)", "`id`", "&& curl evil", "| nc x 1", " spine1",
             "../../etc", "a b", "x\ny"]
# Production container/topology fragments that a twin op must NEVER touch (ISO-1).
PROD_FRAGMENTS = ["clab-clos-evpn-", "clab-minimal-", "clab-3tier-",
                  "de-fra-core-01", "uk-lon-core-01"]


class MockRunner:
    """Records commands; simulates clab deploy/destroy + docker ps/exec."""
    def __init__(self):
        self.calls: list[list[str]] = []
        self.lock = threading.Lock()

    def __call__(self, cmd, timeout=300.0, stdin=None):
        with self.lock:
            self.calls.append(list(cmd))
        if cmd[:2] == ["containerlab", "deploy"]:
            return 0, "deployed", ""
        if cmd[:2] == ["containerlab", "destroy"]:
            return 0, "destroyed", ""
        if cmd[:2] == ["docker", "ps"]:
            # twin_id is embedded in the --filter name=clab-<twin_id>-
            flt = next((a for a in cmd if a.startswith("name=clab-")), "")
            tid = flt[len("name=clab-"):].rstrip("-")
            return 0, f"clab-{tid}-spine1\nclab-{tid}-leaf1\n", ""
        if cmd[:2] == ["docker", "exec"]:
            return 0, "ok", ""
        return 0, "", ""


def _mgr(workdir):
    return TwinManager(runner=MockRunner(), workdir=workdir)


def run(n: int = 4000, tmp: str = "/tmp/aegis-twin-stress") -> dict:
    os.makedirs(tmp, exist_ok=True)
    rng = random.Random(7)
    runner = MockRunner()
    mgr = TwinManager(runner=runner, workdir=tmp)

    res = {k: 0 for k in ("spawn_ok", "spawn_rejected_lab", "spawn_rejected_full",
                          "apply_ok", "apply_bad_device_blocked", "iso1_blocked",
                          "destroy_ok", "destroy_idempotent")}
    failures: list[str] = []
    live: list[str] = []

    def note(cond, msg):
        if not cond:
            failures.append(msg)

    for i in range(n):
        roll = rng.random()
        if roll < 0.30 and len(live) < MAX_TWINS:                       # spawn good
            out = mgr.spawn(rng.choice(list(LAB_TOPOLOGIES)))
            note(out["twin_id"].startswith("twin-"), "spawn id not twin-prefixed")
            live.append(out["twin_id"])
            res["spawn_ok"] += 1
        elif roll < 0.40:                                               # spawn bad lab (ISO-2)
            try:
                mgr.spawn(rng.choice(["prod", "../etc", "clos-evpn; rm", ""]))
                note(False, "bad lab not rejected")
            except TwinError:
                res["spawn_rejected_lab"] += 1
        elif roll < 0.47:                                               # spawn when full (ISO-4)
            while len(live) < MAX_TWINS:
                live.append(mgr.spawn("minimal")["twin_id"])
                res["spawn_ok"] += 1
            try:
                mgr.spawn("minimal")
                note(False, "over-limit spawn not rejected")
            except TwinError as e:
                note(e.status == 429, "wrong status for full")
                res["spawn_rejected_full"] += 1
        elif roll < 0.70 and live:                                      # apply (ISO-3)
            tid = rng.choice(live)
            good = {"device": f"leaf{rng.randint(1,6)}", "vendor": "frr",
                    "config": "router bgp 65010"}
            bad = {"device": rng.choice(INJECTION), "vendor": "frr", "config": "x"}
            out = mgr.apply(tid, [good, bad])
            blocked = [d for d in out["per_device"] if not d["applied"]
                       and d.get("error") == "invalid device name"]
            note(len(blocked) == 1, "injection device not blocked")
            res["apply_ok"] += 1
            res["apply_bad_device_blocked"] += 1
        elif roll < 0.82:                                               # ISO-1 prod isolation
            target = rng.choice(["clab-clos-evpn-spine1", "clos-evpn",
                                 "de-fra-core-01", "twin-ghost-0000"])
            try:
                mgr.apply(target, [{"device": "leaf1", "vendor": "frr", "config": "x"}])
                # only a real live twin should succeed; these are all non-live
                note(False, f"apply on non-twin '{target}' not blocked")
            except TwinError:
                res["iso1_blocked"] += 1
            # destroy path: non-twin must RAISE (ISO-1); unknown twin- is idempotent
            try:
                d = mgr.destroy(target)
                note(target.startswith("twin-") and d["destroyed"] is True,
                     f"destroy of non-twin '{target}' should have raised")
                res["destroy_idempotent"] += 1
            except TwinError:
                note(not target.startswith("twin-"),
                     "destroy of a twin- id should not raise")
                res["iso1_blocked"] += 1
        elif live:                                                      # destroy good (ISO-5)
            tid = live.pop(rng.randrange(len(live)))
            out = mgr.destroy(tid)
            note(out["destroyed"] is True, "destroy failed")
            res["destroy_ok"] += 1

    # tear down remaining
    for tid in list(live):
        mgr.destroy(tid)
    leak = len(mgr.list_twins())

    # ── global command audit: no prod fragment, no injection token, all arg-lists ──
    prod_hits, inj_hits, nonlist = 0, 0, 0
    for cmd in runner.calls:
        if not isinstance(cmd, list):
            nonlist += 1
            continue
        joined = " ".join(cmd)
        if any(p in joined for p in PROD_FRAGMENTS):
            prod_hits += 1
        if any(tok.strip() and tok in joined for tok in INJECTION):
            inj_hits += 1

    # ── concurrency: parallel spawn/apply/destroy, assert no crash/leak ──
    conc_mgr = TwinManager(runner=MockRunner(), workdir=tmp + "-c", max_twins=50)
    conc_err = []
    def worker(k):
        try:
            tid = conc_mgr.spawn("minimal")["twin_id"]
            conc_mgr.apply(tid, [{"device": "leaf1", "vendor": "frr", "config": "x"}])
            conc_mgr.destroy(tid)
        except Exception as e:  # noqa: BLE001
            conc_err.append(repr(e))
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(60)]
    for t in threads: t.start()
    for t in threads: t.join()
    conc_leak = len(conc_mgr.list_twins())

    # ── mgmt isolation: a twin topo must not reuse prod name/network/subnet ──
    iso_mgr = TwinManager(runner=MockRunner(), workdir=tmp + "-iso")
    iso_fail = []
    for lab in ("clos-evpn", "minimal"):
        tid = f"twin-{lab}-isotest"
        with open(iso_mgr._materialize_topo(lab, tid)) as fh:  # noqa: SLF001
            twin_txt = fh.read()
        with open(LAB_TOPOLOGIES[lab]) as fh:
            orig_subnet = (re.search(r"ipv4-subnet:\s*(\S+)", fh.read()) or [None, ""])[1]
        if f"name: {tid}" not in twin_txt:
            iso_fail.append(f"{lab}: name not rewritten")
        if f"{tid}-mgmt" not in twin_txt:
            iso_fail.append(f"{lab}: mgmt network not isolated")
        if "mgmt-ipv4:" in twin_txt:
            iso_fail.append(f"{lab}: static mgmt-ipv4 not stripped")
        if orig_subnet and orig_subnet in twin_txt:
            iso_fail.append(f"{lab}: still uses prod subnet {orig_subnet}")
        if not re.search(r"ipv4-subnet:\s*10\.\d+\.\d+\.0/24", twin_txt):
            iso_fail.append(f"{lab}: no isolated 10.x subnet")

    return {
        "results": res,
        "twin_leak": leak,
        "audit": {"prod_container_hits": prod_hits, "injection_token_hits": inj_hits,
                  "non_arglist_commands": nonlist, "total_commands": len(runner.calls)},
        "concurrency": {"runs": 60, "errors": len(conc_err), "leak": conc_leak,
                        "sample_err": conc_err[:3]},
        "mgmt_isolation": {"ok": not iso_fail, "failures": iso_fail},
        "sample_failures": failures[:10],
    }


def passed(s: dict) -> bool:
    a = s["audit"]
    return (not s["sample_failures"] and s["twin_leak"] == 0
            and a["prod_container_hits"] == 0 and a["injection_token_hits"] == 0
            and a["non_arglist_commands"] == 0
            and s["concurrency"]["errors"] == 0 and s["concurrency"]["leak"] == 0
            and s["mgmt_isolation"]["ok"])


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    t0 = time.perf_counter()
    s = run(n)
    s["wall_sec"] = round(time.perf_counter() - t0, 2)
    print(json.dumps(s, indent=2))
    print("\n=== TWIN STRESS RESULT:", "PASS" if passed(s) else "FAIL", "===")
    sys.exit(0 if passed(s) else 1)
