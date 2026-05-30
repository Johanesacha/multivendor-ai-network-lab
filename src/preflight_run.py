"""preflight_run.py — POST /api/preflight/run : the AEGIS end-to-end loop endpoint.

Drives the AEGIS deterministic pipeline (intent -> guard -> generate -> batfish ->
twin -> diff -> compliance -> verdict -> sealed evidence bundle) and returns the bundle.

  mode="sim"  (default) -> SimulatorBackend : runs anywhere, no Qwen3/clab needed (demo)
  mode="live"           -> HttpBackend      : Qwen3 model-runner + the twin endpoints
                                              on this same :5757 (air-gapped)

Kept as a blueprint so app.py registers it next to mv_bp. The aegis package lives at the
repo root; we add it to sys.path here so app.py needs no change beyond registration.
"""
from __future__ import annotations
import os
import sys

# repo root = .../VSS_Code_Georgi  (src -> DCN_Network_Tool -> 04_Scripts_Tools -> root)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


_UI_PATH = os.path.join(_REPO_ROOT, "aegis", "ui", "preflight_screen.html")


def make_blueprint():
    from flask import Blueprint, jsonify, request, Response
    from aegis.core.orchestrator.pipeline import run_preflight, PreflightError
    from aegis.core.backends.simulator import SimulatorBackend

    bp = Blueprint("preflight_run", __name__)

    @bp.route("/preflight", methods=["GET"])
    def preflight_ui():
        """Serve the PreFlight UI same-origin so it needs no CORS / no file://."""
        try:
            with open(_UI_PATH, encoding="utf-8") as fh:
                return Response(fh.read(), mimetype="text/html")
        except FileNotFoundError:
            return Response("preflight_screen.html not found", status=404)

    @bp.route("/api/preflight/run", methods=["POST"])
    def preflight_run():
        data = request.get_json(silent=True) or {}
        intent = (data.get("intent") or "").strip()
        lab = (data.get("lab") or "clos-evpn").strip()
        frameworks = data.get("frameworks") or ["pci_dss_v4"]
        mode = (data.get("mode") or "sim").strip()
        operator = (data.get("operator") or "ui").strip()
        approver = data.get("approver") or None
        source = (data.get("source") or "nl_intent").strip()

        # config-import: operator pastes a sanitized running-config (no LLM in the loop)
        imported = None
        if source == "config_import":
            if data.get("configs"):
                imported = data["configs"]
            elif (data.get("config") or "").strip():
                imported = [{"device": data.get("device") or "device-1",
                             "vendor": data.get("vendor") or "frr",
                             "config": data["config"]}]
            else:
                return jsonify({"error": "config_import requires 'config' or 'configs'",
                                "stage": "guard"}), 400

        if mode == "live":
            from aegis.core.backends.http_backend import HttpBackend
            # Default to the LLM the app actually runs (Ollama :11434 / gemma4),
            # overridable via env. Ollama serves the OpenAI-compat /v1 endpoint.
            backend = HttpBackend(
                base_url=os.environ.get("AEGIS_BASE", "http://127.0.0.1:5757"),
                model_runner_url=os.environ.get("AEGIS_LLM_URL",
                                                 "http://localhost:11434"),
                model=os.environ.get("AEGIS_LLM_MODEL", "gemma4:latest"),
            )
        else:
            backend = SimulatorBackend()

        try:
            bundle = run_preflight(intent, backend=backend, lab=lab,
                                   frameworks=frameworks, operator=operator,
                                   approver=approver, source=source,
                                   imported_configs=imported)
        except PreflightError as e:
            return jsonify({"error": str(e), "stage": "guard"}), 400
        except Exception as e:  # noqa: BLE001  — surface backend failures cleanly
            # include the exception TYPE so an empty message never hides the cause
            return jsonify({"error": f"{type(e).__name__}: {e}".strip(),
                            "stage": "pipeline", "mode": mode}), 502
        return jsonify(bundle)

    @bp.route("/api/preflight/evidence/pdf", methods=["POST"])
    def evidence_pdf():
        """Render a (already-produced) evidence bundle to an examiner-ready PDF."""
        bundle = request.get_json(silent=True) or {}
        if not bundle.get("integrity", {}).get("sha256"):
            return jsonify({"error": "valid evidence bundle required"}), 400
        try:
            from aegis.evidence.pdf import render_pdf
            pdf = render_pdf(bundle)
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
        run = bundle.get("run_id", "bundle")[:12]
        return Response(pdf, mimetype="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="aegis-evidence-{run}.pdf"'})

    @bp.route("/api/preflight/promote", methods=["POST"])
    def promote_change():
        """Phase 2: promote a verified+approved bundle to prod via a connector.
        Default connector is dry_run (mutates nothing). Live requires explicit opt-in."""
        data = request.get_json(silent=True) or {}
        bundle = data.get("bundle") or {}
        if not bundle.get("integrity", {}).get("sha256"):
            return jsonify({"error": "valid evidence bundle required"}), 400
        from aegis.core.promote.promote import promote, PromoteDenied
        from aegis.core.promote.connectors import get_connector
        try:
            connector = get_connector(data.get("connector") or "dry_run")
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        allow_live = os.environ.get("AEGIS_PROMOTE_ALLOW_LIVE", "").lower() in ("1", "true", "yes")
        try:
            record = promote(bundle, connector=connector,
                             approver=data.get("approver"),
                             approval_token=data.get("approval_token"),
                             allow_live=allow_live)
        except PromoteDenied as e:
            return jsonify({"error": str(e), "stage": "gate", "promoted": False}), 403
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": f"{type(e).__name__}: {e}", "stage": "promote"}), 502
        return jsonify(record)

    return bp
