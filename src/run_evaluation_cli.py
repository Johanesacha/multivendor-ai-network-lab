"""
run_evaluation_cli.py — Command-line launcher for eval_harness multi-model
evaluation campaigns.

Thin CLI wrapper: all the real work is eval_harness.run_campaign() and
eval_harness.generate_report_markdown(). This script only parses arguments,
validates them against scenarios.json / llm_client.MODEL_REGISTRY, and wires
run_campaign's on_progress hook to a console print. It does not reimplement
any evaluation or reporting logic — the web UI's /api/eval/run endpoint
(src/app.py) calls the exact same two functions.

Usage: see README_EVALUATION.md for plain-language Git Bash instructions.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# The validation-substudy report uses emoji/checkmarks (🟢/✅/❌) and Δ that
# Windows' legacy console codepage (cp1252/850) can't encode -- Git Bash
# doesn't fix this on its own since Python still falls back to
# locale.getpreferredencoding() when stdout isn't a real Win32 console.
# Reconfigure defensively so this never crashes a demo; harmless no-op on
# platforms that are already UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

_HERE = Path(__file__).resolve().parent

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_HERE / ".env")

import eval_harness  # noqa: E402
import llm_client  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_evaluation_cli.py",
        description=(
            "Lance une campagne d'évaluation multi-modèle (eval_harness) en ligne de "
            "commande. Sans arguments : tous les scénarios x tous les modèles x 3 répétitions."
        ),
    )
    p.add_argument(
        "--scenarios", type=str, default=None,
        help="IDs de scénarios séparés par des virgules (ex: bgp-001,ospf-001). "
             "Défaut : tous les scénarios de scenarios.json.",
    )
    p.add_argument(
        "--models", type=str, default=None,
        help="IDs de modèles séparés par des virgules (ex: claude-haiku-4-5,qwen2.5:3b). "
             "Défaut : tous les modèles de llm_client.MODEL_REGISTRY.",
    )
    p.add_argument(
        "--repeats", type=int, default=3,
        help="Nombre de répétitions par couple (scénario, modèle). Défaut : 3.",
    )
    p.add_argument(
        "--agent", type=str, default="ai_command", choices=["ai_command", "orchestrator"],
        help="Agent à tester. Défaut : ai_command.",
    )
    p.add_argument(
        "--output-dir", type=str, default=None,
        help="Dossier de sortie pour le JSONL et le rapport. Défaut : src/campaign_results/.",
    )
    p.add_argument(
        "--list", action="store_true",
        help="Affiche les scénarios et modèles disponibles, puis quitte (aucun run lancé).",
    )
    p.add_argument(
        "--validation-substudy", action="store_true",
        help=(
            "Lance la sous-étude de validation live-vs-synthétique (recommandée par la revue "
            "méthodologique externe) au lieu d'une campagne normale : pour les 5 scénarios "
            "live-capables (bgp-001, bgp-002, ospf-001, intf-001, intent-001) x les modèles "
            "choisis (--models, défaut : tous), interroge réellement les routeurs FRR via "
            "docker exec/vtysh au lieu du texte synthétique habituel, et compare au score déjà "
            "enregistré dans les campagnes principales. Ignore --scenarios et --repeats (fixés "
            "par la sous-étude : les 5 scénarios ci-dessus, 1 run par cellule). Voir "
            "VALIDATION_SUBSTUDY_GUIDE.md."
        ),
    )
    return p.parse_args(argv)


def _validate_ids(requested: list[str], available: list[str], label: str) -> list[str] | None:
    unknown = [x for x in requested if x not in available]
    if unknown:
        print(f"Erreur : {label}(s) inconnu(s) : {', '.join(unknown)}")
        print(f"{label}s disponibles : {', '.join(available)}")
        return None
    return requested


def _run_validation_substudy(args: argparse.Namespace, all_model_ids: list[str], out_dir: Path) -> int:
    """--validation-substudy branch. Mirrors main()'s campaign flow (validate
    -> run -> write JSONL -> render + save + print report) but calls
    eval_harness.run_validation_substudy()/generate_validation_substudy_report_markdown()
    instead of the main-campaign pair -- same shared-function contract the
    web UI's /api/eval/validation-substudy endpoint uses, so CLI and UI can
    never produce different numbers from the same underlying data.
    """
    if args.models:
        model_ids = _validate_ids(
            [m.strip() for m in args.models.split(",") if m.strip()],
            all_model_ids, "modèle",
        )
        if model_ids is None:
            return 1
    else:
        model_ids = all_model_ids

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = out_dir / f"validation-substudy-{ts}.jsonl"
    report_path = out_dir / f"validation-substudy-{ts}-rapport.md"

    scenario_ids = eval_harness.LIVE_CAPABLE_SCENARIOS
    total = len(scenario_ids) * len(model_ids)
    print(f"Scénarios live-capables ({len(scenario_ids)}) : {', '.join(scenario_ids)}")
    print(f"Modèles ({len(model_ids)}) : {', '.join(model_ids)}")
    print(f"1 run live par cellule -> {total} run(s) au total")
    print(f"Résultats bruts (JSONL) : {jsonl_path}")
    print("Démarrage... chaque cellule interroge réellement les routeurs FRR (docker exec/vtysh) "
          "puis appelle le modèle -- les modèles locaux peuvent être lents.")
    print()

    def _progress(done: int, tot: int, result: dict) -> None:
        cmp = result.get("comparison") or {}
        print(
            f"  [{done}/{tot}] {result.get('scenario_id')} / {result.get('agent_model_id')} "
            f"-> {result.get('agent_path')} "
            f"(synthétique={cmp.get('synthetic_keyword_score')} live={cmp.get('live_keyword_score')} "
            f"accord={cmp.get('keyword_bucket_agree')})",
            flush=True,
        )

    path = eval_harness.run_validation_substudy(
        model_ids=model_ids, agent=args.agent, output_path=str(jsonl_path), on_progress=_progress,
    )

    with open(path, encoding="utf-8") as f:
        results = [json.loads(line) for line in f]

    report = eval_harness.generate_validation_substudy_report_markdown(results)
    report_path.write_text(report, encoding="utf-8")

    print()
    print("=" * 70)
    print("TERMINÉ")
    print(f"  Résultats bruts (JSONL) : {jsonl_path}")
    print(f"  Rapport de synthèse (Markdown) : {report_path}")
    print("=" * 70)
    print()
    print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    all_scenarios = eval_harness.load_scenarios()
    all_scenario_ids = [s["id"] for s in all_scenarios]
    all_model_ids = list(llm_client.MODEL_REGISTRY.keys())

    if args.list:
        print("Scénarios disponibles :")
        for s in all_scenarios:
            print(f"  {s['id']:<14} {s['title']}")
        print()
        print("Modèles disponibles :")
        for mid, entry in llm_client.MODEL_REGISTRY.items():
            print(f"  {mid:<18} ({entry['provider']})")
        return 0

    if args.validation_substudy:
        if args.scenarios:
            print("Note : --scenarios est ignoré par --validation-substudy "
                  f"(scénarios fixés : {', '.join(eval_harness.LIVE_CAPABLE_SCENARIOS)}).")
        if args.repeats != 3:  # 3 is the parser default -- only warn on an explicit override
            print("Note : --repeats est ignoré par --validation-substudy (1 run par cellule).")
        return _run_validation_substudy(args, all_model_ids, out_dir=(
            Path(args.output_dir) if args.output_dir else _HERE / "campaign_results"
        ))

    if args.scenarios:
        scenario_ids = _validate_ids(
            [s.strip() for s in args.scenarios.split(",") if s.strip()],
            all_scenario_ids, "scénario",
        )
        if scenario_ids is None:
            return 1
    else:
        scenario_ids = all_scenario_ids

    if args.models:
        model_ids = _validate_ids(
            [m.strip() for m in args.models.split(",") if m.strip()],
            all_model_ids, "modèle",
        )
        if model_ids is None:
            return 1
    else:
        model_ids = all_model_ids

    if args.repeats < 1:
        print("Erreur : --repeats doit être >= 1")
        return 1

    out_dir = Path(args.output_dir) if args.output_dir else _HERE / "campaign_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = out_dir / f"campaign-{ts}.jsonl"
    report_path = out_dir / f"campaign-{ts}-rapport.md"

    total = len(scenario_ids) * len(model_ids) * args.repeats
    print(f"Scénarios ({len(scenario_ids)}) : {', '.join(scenario_ids)}")
    print(f"Modèles ({len(model_ids)}) : {', '.join(model_ids)}")
    print(f"Répétitions : {args.repeats}  ->  {total} run(s) au total")
    print(f"Résultats bruts (JSONL) : {jsonl_path}")
    print("Démarrage... (les modèles locaux peuvent être lents, ça peut prendre du temps)")
    print()

    def _progress(done: int, tot: int, result: dict) -> None:
        print(
            f"  [{done}/{tot}] {result.get('scenario_id')} / {result.get('agent_model_id')} "
            f"-> {result.get('agent_path')}",
            flush=True,
        )

    path = eval_harness.run_campaign(
        scenario_ids, model_ids, repeats=args.repeats,
        agent=args.agent, output_path=str(jsonl_path), on_progress=_progress,
    )

    with open(path, encoding="utf-8") as f:
        results = [json.loads(line) for line in f]

    report = eval_harness.generate_report_markdown(
        results, title="Rapport d'évaluation multi-modèle"
    )
    report_path.write_text(report, encoding="utf-8")

    print()
    print("=" * 70)
    print("TERMINÉ")
    print(f"  Résultats bruts (JSONL) : {jsonl_path}")
    print(f"  Rapport de synthèse (Markdown) : {report_path}")
    print("=" * 70)
    print()
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
