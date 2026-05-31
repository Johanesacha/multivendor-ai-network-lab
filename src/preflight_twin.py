"""preflight_twin.py — throwaway containerlab twins for AEGIS pre-deployment validation.

Exposes three NET-NEW write endpoints (the rest of :5757 is read-only):

    POST /api/preflight/twin/spawn    {lab}              -> {twin_id, lab, nodes, status}
    POST /api/preflight/twin/apply    {twin_id, configs} -> {applied, per_device[]}
    POST /api/preflight/twin/destroy  {twin_id}          -> {destroyed}

EVERY twin is a clone of a reference topology deployed under a unique `twin-…` name, so
its containers are `clab-twin-…-<node>` and can NEVER collide with the production labs
(`clab-clos-evpn-*`, the FRR DCN lab, real devices).

Safety invariants (enforced here, stress-tested in tests/test_preflight_twin.py):
  ISO-1  apply/destroy reject any id that is not a known `twin-` id  -> prod isolation
  ISO-2  spawn only accepts labs in the allowlist                    -> no path injection
  ISO-3  device names must match ^[A-Za-z0-9_.:-]+$                  -> no shell injection
  ISO-4  at most MAX_TWINS live at once                              -> host protection
  ISO-5  destroy is idempotent (unknown/already-gone -> ok)          -> no leaks
  ISO-6  all commands run as arg-lists, never shell=True             -> no injection

All containerlab/docker calls go through an injectable `runner` so the logic is testable
with no Docker present. Default runner shells out via subprocess (arg-list, no shell).
"""
from __future__ import annotations
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOPO_DIR = os.path.abspath(
    os.path.join(_HERE, "..", "containerlab-multivendor", "topologies"))
_MV_DIR = os.path.dirname(_TOPO_DIR)   # containerlab-multivendor (holds ../configs)


def _resolve_bin(name: str, env_var: str, candidates: list[str]) -> str:
    """Find a binary even when the daemon's PATH is stripped (nohup/launchd).

    Order: explicit env override -> PATH (shutil.which) -> common install dirs ->
    bare name (will FileNotFoundError with a clear message if truly absent).
    """
    override = os.environ.get(env_var)
    if override and os.path.exists(override):
        return override
    found = shutil.which(name)
    if found:
        return found
    for c in candidates:
        if os.path.exists(c):
            return c
    return name


_CLAB_BIN = _resolve_bin("containerlab", "AEGIS_CLAB_BIN", [
    "/usr/local/bin/containerlab", "/opt/homebrew/bin/containerlab",
    "/usr/bin/containerlab",
    # containerlab also installs a short `clab` alias
    "/usr/local/bin/clab", "/opt/homebrew/bin/clab", "/usr/bin/clab"])
_DOCKER_BIN = _resolve_bin("docker", "AEGIS_DOCKER_BIN", [
    "/usr/local/bin/docker", "/opt/homebrew/bin/docker", "/usr/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker"])

# This repo's deploy.sh runs `sudo containerlab deploy`. If clab needs root here,
# set AEGIS_CLAB_SUDO=1 (and a passwordless sudoers entry for the clab binary).
_CLAB_SUDO = os.environ.get("AEGIS_CLAB_SUDO", "").lower() in ("1", "true", "yes")

# On macOS there is usually NO host containerlab binary — it runs as a Docker image
# (ghcr.io/srl-labs/clab) talking to the Docker socket. Set AEGIS_CLAB_MODE=docker.
#   binary : call _CLAB_BIN directly (Linux host / sudo)
#   docker : run the clab image, socket + topology dir mounted (macOS Docker Desktop)
#   auto   : docker if no clab binary was found, else binary
_CLAB_MODE = os.environ.get("AEGIS_CLAB_MODE", "auto").lower()
_CLAB_IMAGE = os.environ.get("AEGIS_CLAB_IMAGE", "ghcr.io/srl-labs/clab:latest")
_DOCKER_SOCK = os.environ.get(
    "AEGIS_DOCKER_SOCK", os.path.expanduser("~/.docker/run/docker.sock"))


def _use_docker_clab() -> bool:
    if _CLAB_MODE == "docker":
        return True
    if _CLAB_MODE == "binary":
        return False
    # auto: use docker when we never resolved a real clab binary
    return not (os.path.sep in _CLAB_BIN and os.path.exists(_CLAB_BIN))


def _clab_cmd(*args: str) -> list[str]:
    """Build a clab command for either a host binary or the containerlab Docker image.

    Docker mode mirrors the standard macOS containerlab-in-docker invocation: mount the
    Docker socket so clab can drive Docker Desktop, and mount the topologies tree at the
    same path so the .clab.yml and its relative `../configs` startup-configs resolve.
    """
    if _use_docker_clab():
        # the clab image's entrypoint is NOT containerlab, so name the binary explicitly
        inner = os.environ.get("AEGIS_CLAB_INNER_CMD", "containerlab")
        return [
            _DOCKER_BIN, "run", "--rm", "--privileged", "--network", "host",
            "-v", f"{_DOCKER_SOCK}:/var/run/docker.sock",
            "-v", f"{_MV_DIR}:{_MV_DIR}", "-w", _TOPO_DIR,
            _CLAB_IMAGE, inner, *args,
        ]
    return (["sudo", "-n"] if _CLAB_SUDO else []) + [_CLAB_BIN, *args]

# ISO-2: only these labs may be spawned, mapped to their real topology files.
LAB_TOPOLOGIES = {
    "clos-evpn": os.path.join(_TOPO_DIR, "clos-evpn.clab.yml"),
    "minimal":   os.path.join(_TOPO_DIR, "minimal.clab.yml"),
    "3tier":     os.path.join(_TOPO_DIR, "3tier-enterprise.clab.yml"),
}

MAX_TWINS = 4                      # ISO-4
_DEVICE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")   # ISO-3
_TWIN_PREFIX = "twin-"


def _default_runner(cmd: list[str], timeout: float = 300.0) -> tuple[int, str, str]:
    """Execute an arg-list (ISO-6: never shell=True). Returns (rc, stdout, stderr)."""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
    return p.returncode, p.stdout, p.stderr


@dataclass
class _Twin:
    twin_id: str
    lab: str
    topo_path: str
    nodes: list[str] = field(default_factory=list)


class TwinError(Exception):
    """Raised for any guard violation; the blueprint maps it to a 4xx."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class TwinManager:
    def __init__(self, runner=_default_runner, workdir: str | None = None,
                 max_twins: int = MAX_TWINS):
        self._runner = runner
        # Default to the topologies dir so the twin file sits beside the original and
        # its relative `startup-config: ../configs/...` paths still resolve. Tests pass
        # an explicit /tmp workdir (their mock runner never reads the file).
        self._workdir = workdir or _TOPO_DIR
        self._max = max_twins
        self._twins: dict[str, _Twin] = {}
        self._lock = threading.Lock()
        os.makedirs(self._workdir, exist_ok=True)

    # ── spawn ────────────────────────────────────────────────────────────
    def spawn(self, lab: str) -> dict:
        if lab not in LAB_TOPOLOGIES:                                   # ISO-2
            raise TwinError(f"unknown lab '{lab}'; allowed: {sorted(LAB_TOPOLOGIES)}")
        with self._lock:
            if len(self._twins) >= self._max:                          # ISO-4
                raise TwinError(f"twin limit reached ({self._max} live); destroy one first",
                                status=429)
            twin_id = f"{_TWIN_PREFIX}{lab}-{secrets.token_hex(4)}"
            topo_path = self._materialize_topo(lab, twin_id)
            self._twins[twin_id] = _Twin(twin_id, lab, topo_path)

        rc, out, err = self._runner(_clab_cmd("deploy", "-t", topo_path,
                                              "--reconfigure"))
        if rc != 0:
            self._forget(twin_id)
            raise TwinError(f"clab deploy failed: {err or out}", status=500)
        nodes = self._discover_nodes(twin_id)
        with self._lock:
            if twin_id in self._twins:
                self._twins[twin_id].nodes = nodes
        return {"twin_id": twin_id, "lab": lab, "nodes": nodes, "status": "deployed"}

    # ── apply ────────────────────────────────────────────────────────────
    def apply(self, twin_id: str, configs: list[dict]) -> dict:
        twin = self._require_twin(twin_id)                             # ISO-1
        per_device, ok_all = [], True
        for c in configs or []:
            device = str(c.get("device", ""))
            if not _DEVICE_RE.match(device):                           # ISO-3
                per_device.append({"device": device, "applied": False,
                                   "error": "invalid device name"})
                ok_all = False
                continue
            container = f"clab-{twin_id}-{device}"
            cfg = c.get("config", "")
            rc, out, err = self._push(container, c.get("vendor", "frr"), cfg)
            applied = rc == 0
            ok_all = ok_all and applied
            per_device.append({"device": device, "container": container,
                               "applied": applied,
                               "error": None if applied else (err or out)[:200]})
        return {"applied": ok_all, "per_device": per_device}

    # ── destroy (idempotent) ──────────────────────────────────────────────
    def destroy(self, twin_id: str) -> dict:
        if not twin_id.startswith(_TWIN_PREFIX):                       # ISO-1
            raise TwinError("refusing to destroy a non-twin id (production isolation)",
                            status=403)
        twin = self._twins.get(twin_id)
        if twin is None:                                               # ISO-5
            return {"destroyed": True, "note": "unknown twin (already gone)"}
        rc, out, err = self._runner(_clab_cmd("destroy", "-t", twin.topo_path,
                                              "--cleanup"))
        self._forget(twin_id)
        return {"destroyed": rc == 0, "detail": (err or out)[:200] if rc else "ok"}

    def list_twins(self) -> list[str]:
        with self._lock:
            return sorted(self._twins)

    # ── internals ─────────────────────────────────────────────────────────
    def _require_twin(self, twin_id: str) -> _Twin:
        if not twin_id.startswith(_TWIN_PREFIX):
            raise TwinError("id is not a twin (production isolation)", status=403)
        twin = self._twins.get(twin_id)
        if twin is None:
            raise TwinError(f"no live twin '{twin_id}'", status=404)
        return twin

    def _materialize_topo(self, lab: str, twin_id: str) -> str:
        """Copy the reference topology and isolate it so the twin can run ALONGSIDE the
        live prod lab without collision:
          - name           -> unique twin id (container prefix clab-twin-…)
          - mgmt.network    -> unique docker network name
          - mgmt.ipv4-subnet-> deterministic unique /24 (derived from twin id)
          - per-node mgmt-ipv4 lines stripped -> clab auto-assigns in the new subnet
        Fabric configs (loopbacks/BGP/EVPN) are untouched; only the clab mgmt plane moves.
        """
        src = LAB_TOPOLOGIES[lab]
        dst = os.path.join(self._workdir, f"{twin_id}.clab.yml")
        with open(src) as fh:
            text = fh.read()

        h = int(hashlib.sha256(twin_id.encode()).hexdigest()[:8], 16)
        subnet = f"10.{100 + (h % 100)}.{(h // 100) % 256}.0/24"   # 10.100-199.x.0/24

        text = re.sub(r"(?m)^name:\s*.*$", f"name: {twin_id}", text, count=1)
        text = re.sub(r"(?m)^(\s*)network:\s*.*$",
                      lambda m: f"{m.group(1)}network: {twin_id}-mgmt", text, count=1)
        text = re.sub(r"(?m)^(\s*)ipv4-subnet:\s*.*$",
                      lambda m: f"{m.group(1)}ipv4-subnet: {subnet}", text, count=1)
        # drop static mgmt IPs (old subnet) so clab assigns from the twin's subnet
        text = re.sub(r"(?m)^\s*mgmt-ipv4:\s*.*\n", "", text)

        with open(dst, "w") as fh:
            fh.write(text)
        return dst

    def _discover_nodes(self, twin_id: str) -> list[str]:
        rc, out, _ = self._runner([_DOCKER_BIN, "ps", "--format", "{{.Names}}",
                                   "--filter", f"name=clab-{twin_id}-"])
        if rc != 0:
            return []
        prefix = f"clab-{twin_id}-"
        return sorted(n[len(prefix):] for n in out.split() if n.startswith(prefix))

    def _push(self, container: str, vendor: str, config: str) -> tuple[int, str, str]:
        """Push candidate config into a twin node. vtysh for FRR, Cli/sr_cli otherwise."""
        if vendor == "frr":
            cmd = [_DOCKER_BIN, "exec", "-i", container, "vtysh"]
            return self._runner_stdin(cmd, "configure terminal\n" + config + "\nend\n")
        if vendor == "srlinux":
            cmd = [_DOCKER_BIN, "exec", "-i", container, "sr_cli"]
            return self._runner_stdin(cmd, config)
        # ceos / others: Cli config session
        cmd = [_DOCKER_BIN, "exec", "-i", container, "Cli", "-p", "15"]
        return self._runner_stdin(cmd, "configure\n" + config + "\nend\n")

    def _runner_stdin(self, cmd: list[str], stdin_text: str) -> tuple[int, str, str]:
        # default runner ignores stdin; subprocess override handles it. Tests inject.
        try:
            return self._runner(cmd, stdin=stdin_text)  # type: ignore[call-arg]
        except TypeError:
            return self._runner(cmd)

    def _forget(self, twin_id: str) -> None:
        with self._lock:
            twin = self._twins.pop(twin_id, None)
        if twin and os.path.exists(twin.topo_path):
            try:
                os.remove(twin.topo_path)
            except OSError:
                pass


# ── Flask blueprint (lazy import so the module is testable without Flask) ──
def make_blueprint(manager: TwinManager | None = None):
    from flask import Blueprint, jsonify, request
    mgr = manager or TwinManager()
    bp = Blueprint("preflight_twin", __name__)

    def _err(e: TwinError):
        return jsonify({"error": str(e)}), e.status

    def _crash(e: Exception):
        # any non-guard failure (e.g. containerlab not on PATH) -> clean JSON 500,
        # never a Flask HTML error page (which breaks downstream JSON parsers)
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

    @bp.route("/api/preflight/twin/spawn", methods=["POST"])
    def spawn():
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(mgr.spawn((data.get("lab") or "").strip()))
        except TwinError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return _crash(e)

    @bp.route("/api/preflight/twin/apply", methods=["POST"])
    def apply():
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(mgr.apply((data.get("twin_id") or "").strip(),
                                     data.get("configs") or []))
        except TwinError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return _crash(e)

    @bp.route("/api/preflight/twin/destroy", methods=["POST"])
    def destroy():
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(mgr.destroy((data.get("twin_id") or "").strip()))
        except TwinError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return _crash(e)

    return bp

# Register in app.py next to the other blueprint:
#     from preflight_twin import make_blueprint as _preflight_bp
#     app.register_blueprint(_preflight_bp())
