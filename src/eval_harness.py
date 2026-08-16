"""
eval_harness.py — Run AI-driven diagnoses against pre-defined incident scenarios
and score them with an LLM-as-judge.

Inspired by NIKA. Each scenario in scenarios.json declares:
  - injected fault (type, device, params)
  - expected root cause keywords
  - expected remediation keywords

Workflow:
  1. Inject the fault (sim layer is best-effort; for some faults we just
     describe the symptom to the agent without actually modifying state).
  2. Hand the symptom to the agent under test (orchestrator | ai_command).
  3. Compare agent output keywords vs expected via a simple keyword overlap
     score, then optionally an LLM judge for a richer 0–10 score.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Callable

import gait_audit
import llm_client

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCENARIOS_FILE = os.path.join(_HERE, "scenarios.json")


def load_scenarios() -> list[dict[str, Any]]:
    with open(_SCENARIOS_FILE) as f:
        return json.load(f)["scenarios"]


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    for s in load_scenarios():
        if s["id"] == scenario_id:
            return s
    return None


def _keyword_overlap(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    """Return (count, hits) of keywords found in text as whole words/phrases.

    Two fixes over a plain substring check:
      - \\b word-boundary matching, so "AS" doesn't match inside "was"/"has"/
        "last", "RCE" doesn't match inside "source", and "22" doesn't match
        inside "2022".
      - acronyms/identifiers (keyword is all-uppercase, e.g. "AS"/"RCE"/"MTU",
        or contains a digit, e.g. "22"/"CVE-2022-22241") are matched
        case-sensitively. Without this, a case-insensitive whole-word match on
        "AS" still hits the ordinary English word "as" ("reported as ..."),
        which word-boundaries alone don't fix.
    """
    hits = []
    for k in keywords:
        if not k:
            continue
        pattern = r"\b" + re.escape(k) + r"\b"
        is_identifier = k.isupper() or any(c.isdigit() for c in k)
        flags = 0 if is_identifier else re.IGNORECASE
        if re.search(pattern, text, flags):
            hits.append(k)
    return len(hits), hits


# Reference length (chars) for a concise, well-formed diagnosis. Beyond this,
# length_controlled_score in keyword_score() discounts proportionally — a
# longer answer has a higher chance of incidentally containing more of the
# expected keywords (restating context, listing every plausible cause) without
# being more correct, and the raw score has no way to penalize that padding.
_LENGTH_BUDGET_CHARS = 800


def keyword_score(agent_output: str, scenario: dict[str, Any]) -> dict[str, Any]:
    """Cheap, deterministic 0–10 score based on keyword overlap.

    Reports two scores: `score` (raw overlap, unchanged formula) and
    `length_controlled_score` (the same overlap, discounted once the output
    exceeds _LENGTH_BUDGET_CHARS) — see the module-level comment on
    _LENGTH_BUDGET_CHARS for why. Both are kept so campaigns can compare
    models on either basis rather than the harness silently picking one.
    """
    rc_kw = scenario.get("expected_root_cause_keywords", [])
    rm_kw = scenario.get("expected_remediation_keywords", [])
    rc_hit, rc_hits = _keyword_overlap(agent_output, rc_kw)
    rm_hit, rm_hits = _keyword_overlap(agent_output, rm_kw)

    rc_score = (rc_hit / max(len(rc_kw), 1)) * 6.0   # weight root-cause higher
    rm_score = (rm_hit / max(len(rm_kw), 1)) * 4.0
    score = round(rc_score + rm_score, 2)

    length_penalty = min(1.0, _LENGTH_BUDGET_CHARS / max(len(agent_output), _LENGTH_BUDGET_CHARS))
    length_controlled_score = round(score * length_penalty, 2)

    return {
        "score": score,
        "length_controlled_score": length_controlled_score,
        "max": 10,
        "root_cause_hits": rc_hits,
        "remediation_hits": rm_hits,
        "output_chars": len(agent_output),
        "method": "keyword",
    }


# Model used for the LLM-as-judge. Exposed as a module constant (rather than
# a string only inlined in the API call) so aggregate_results() and any
# consumer of a run's llm_score.model can report it without re-deriving it.
#
# LIMITATION — self-preference bias: this is the SAME model _ai_command_sync()
# uses to answer the "ai_command" agent path by default (and the default for
# pydantic_ai_orchestrator._call_claude too). A model judging its own family's
# output is a known source of self-preference bias in LLM-as-judge setups —
# the judge may systematically score claude-haiku-4-5 diagnoses more
# favorably than an equally-good diagnosis from a different model family.
# Not corrected here (would need an independent judge model); flagged so
# campaign results are interpreted with this in mind.
JUDGE_MODEL = "claude-haiku-4-5"


def llm_judge(
    symptom: str, agent_output: str, scenario: dict[str, Any], judge_model_id: str = JUDGE_MODEL
) -> dict[str, Any] | None:
    """
    Use an LLM (default claude-haiku-4-5, via llm_client.query) to score 0–10
    with reasoning. On success the returned dict includes a `usage` field
    with input/output tokens.

    judge_model_id stays fixed at claude-haiku-4-5 across model-comparison
    campaigns (see run_scenario) so judge scores remain comparable between
    runs of different agent models — only the agent under test should vary.

    The judge sees the symptom and a generic scoring rubric — NOT the
    scenario's expected_root_cause_keywords / expected_remediation_keywords
    (the answer key used by keyword_score()) or the scenario title, both of
    which would leak the intended answer into an evaluation that's supposed
    to be independent of it.
    """
    prompt = (
        "You are an expert network engineer judging an AI agent's incident "
        "diagnosis. You do NOT have an answer key — judge the diagnosis on its "
        "own technical merits against the reported symptom.\n\n"
        f"Category: {scenario['category']}\n"
        f"Severity: {scenario.get('severity', 'unknown')}\n\n"
        f"Reported symptom:\n{symptom}\n\n"
        f"Agent's diagnosis:\n{agent_output}\n\n"
        "Score the diagnosis from 0 to 10 (10 = perfect) using this rubric:\n"
        "  - Root cause (60%): does the stated root cause plausibly and "
        "specifically explain the reported symptom — not a restatement of the "
        "symptom itself, and not a generic list of possible causes?\n"
        "  - Remediation (30%): are the proposed steps concrete, actionable, "
        "and directly tied to the stated root cause — not generic "
        "troubleshooting boilerplate that would apply to any incident?\n"
        "  - Grounding (10%): no hallucinated devices, peers, IPs, or facts "
        "not supported by the symptom.\n\n"
        "Respond with strict JSON on a single line: "
        "{\"score\": <0-10>, \"reasoning\": \"...\"}. "
        "The reasoning value must not contain literal newlines — write it as "
        "one line, or use \\n if you need to represent a line break within it."
    )
    result = llm_client.query(judge_model_id, prompt, max_tokens=1000, timeout_s=60)
    usage = {"input": result.tokens.get("input", 0), "output": result.tokens.get("output", 0)}

    if result.error or not result.text:
        logger.warning("llm_judge failed: %s", result.error)
        return {
            "score": 0, "reasoning": f"judge error: {result.error}", "method": "llm_judge",
            "model": judge_model_id, "max": 10, "error": True, "usage": usage,
        }

    m = re.search(r"\{[\s\S]*\}", result.text)
    if not m:
        return {"score": 0, "method": "llm_judge", "model": judge_model_id, "max": 10, "error": True,
                "usage": usage}
    try:
        # strict=False tolerates literal control characters (unescaped
        # newlines/tabs) inside JSON string values — the judge is asked to
        # avoid them (see prompt above), but LLM output isn't guaranteed to
        # comply, and this is the stdlib's built-in tolerant-parsing mode
        # rather than a bespoke cleanup regex that only handles \n.
        parsed = json.loads(m.group(0), strict=False)
    except json.JSONDecodeError as e:
        logger.warning("llm_judge failed: %s", e)
        return {"score": 0, "reasoning": f"judge JSON parse error: {e}", "method": "llm_judge",
                "model": judge_model_id, "max": 10, "error": True, "usage": usage}
    parsed["method"] = "llm_judge"
    parsed["model"] = judge_model_id
    parsed["max"] = 10
    parsed["usage"] = usage
    return parsed


def synthesize_symptom(scenario: dict[str, Any]) -> str:
    """
    Translate the scenario's structured fault into a natural-language symptom
    that we hand to the agent. (We don't actually break the lab on every run.)
    """
    f = scenario["fault"]
    t = f["type"]
    if t == "bgp_peer_down":
        return (
            f"The BGP peer {f['peer_hostname']} ({f['peer_ip']}) on device "
            f"{f['device']} is reporting state Idle. It was Established 5 minutes ago. "
            "Diagnose root cause and propose a remediation."
        )
    if t == "bgp_as_mismatch":
        return (
            f"BGP session on {f['device']} towards {f['peer_hostname']} is failing. "
            f"Local config says remote-as {f['configured_as']}, but peer reports its AS as {f['expected_as']}. "
            "Diagnose and fix."
        )
    if t == "ospf_area_mismatch":
        return (
            f"OSPF adjacency between {f['device']} and {f['peer_hostname']} is stuck in ExStart. "
            f"Local interface is in area {f['configured_area']}, peer in {f['expected_area']}. "
            "Diagnose and fix."
        )
    if t == "interface_down":
        return (
            f"Interface {f['interface']} on {f['device']} is down/down. "
            "No optic alarm. Diagnose and propose remediation."
        )
    if t == "mtu_mismatch":
        return (
            f"BGP session between {f['device_a']} (MTU {f['mtu_a']}) and "
            f"{f['device_b']} (MTU {f['mtu_b']}) flaps every 30s with 'hold timer expired'. "
            "Diagnose."
        )
    if t == "acl_block":
        return (
            f"User reports management traffic from {f['src']} to TCP/{f['dst_port']} "
            f"is being denied at {f['device']}. Policy {f['policy']} is suspected. Diagnose and propose fix."
        )
    if t == "acl_overpermissive":
        return (
            f"Audit reports policy {f['policy']} on {f['device']} as overly permissive (any/any). "
            "Recommend a tighter rule."
        )
    if t == "cve_present":
        return (
            f"Device {f['device']} runs an OS version vulnerable to {f['cve_id']}. "
            "Recommend remediation."
        )
    if t == "intent_drift":
        return (
            f"NetBox claims {f['device']} should peer with {f['claimed_peer']}, "
            "but device shows no such neighbor. Diagnose drift and fix."
        )
    if t == "high_cpu":
        return (
            f"Device {f['device']} reports CPU at {f['cpu_pct']}%. "
            f"Suspected cause: {f['cause']}. Diagnose and recommend mitigation."
        )
    return f"Unknown fault type: {t}. Raw payload: {json.dumps(f)}"


def run_scenario(
    scenario_id: str,
    agent: str = "ai_command",
    agent_model_id: str = "claude-haiku-4-5",
    judge_model_id: str = "claude-haiku-4-5",
) -> dict[str, Any]:
    """
    Run a single scenario end-to-end. The actual agent invocation is delegated
    to a callable resolved lazily (we only import here to avoid cycles).

    agent_model_id selects the model under test (any MODEL_REGISTRY key in
    llm_client — Claude or a local Ollama model); default is claude-haiku-4-5
    for full backward compatibility. judge_model_id defaults to (and normally
    stays) claude-haiku-4-5 regardless of agent_model_id, so judge scores stay
    comparable across model-comparison runs — only the agent under test should
    vary between runs being compared.

    Returns dict with: {symptom, agent_output, agent_path, stub_reason,
    keyword_score, llm_score?, total_ms, scenario, model_cost,
    eval_overhead_cost, agent_model_id, judge_model_id}
    """
    scenario = get_scenario(scenario_id)
    if not scenario:
        return {"error": f"Unknown scenario: {scenario_id}"}

    symptom = synthesize_symptom(scenario)
    t0 = time.time()

    agent_output, agent_usage, agent_path, stub_reason = _invoke_agent_with_usage(
        agent, symptom, agent_model_id
    )
    elapsed_ms = int((time.time() - t0) * 1000)

    kscore = keyword_score(agent_output, scenario)
    jscore = llm_judge(symptom, agent_output, scenario, judge_model_id)
    judge_usage = (jscore or {}).get("usage", {"input": 0, "output": 0}) if isinstance(jscore, dict) else {"input": 0, "output": 0}

    # Kept separate rather than blended: "how much did running this agent
    # cost" and "how much did evaluating it cost" are different questions,
    # and a blended number can't answer either one (review item 5).
    model_cost = {"input": int(agent_usage.get("input", 0)), "output": int(agent_usage.get("output", 0))}
    eval_overhead_cost = {"input": int(judge_usage.get("input", 0)), "output": int(judge_usage.get("output", 0))}
    total_usage = {
        "input":  model_cost["input"] + eval_overhead_cost["input"],
        "output": model_cost["output"] + eval_overhead_cost["output"],
    }

    result = {
        "scenario_id": scenario_id,
        "scenario": {"title": scenario["title"], "category": scenario["category"], "severity": scenario["severity"]},
        "agent": agent,
        "agent_model_id": agent_model_id,
        "judge_model_id": judge_model_id,
        "agent_path": agent_path,
        "stub_reason": stub_reason,
        "symptom": symptom,
        "agent_output": agent_output,
        "keyword_score": kscore,
        "llm_score": jscore,
        "total_ms": elapsed_ms,
        "model_cost": model_cost,
        "eval_overhead_cost": eval_overhead_cost,
    }

    gait_audit.record(
        actor="eval_harness",
        action="run_scenario",
        target=scenario["fault"].get("device"),
        prompt=symptom,
        response=agent_output[:500],
        tools_called=[agent],
        tokens=total_usage,
        status="ok",
        extra={
            "scenario_id": scenario_id,
            "score": kscore["score"],
            "agent_path": agent_path,
            "agent_model_id": agent_model_id,
            "judge_model_id": judge_model_id,
        },
    )
    return result


_CAMPAIGN_RESULTS_DIR = os.path.join(_HERE, "campaign_results")


def run_campaign(
    scenario_ids: list[str],
    model_ids: list[str],
    repeats: int = 3,
    agent: str = "ai_command",
    judge_model_id: str = "claude-haiku-4-5",
    output_path: str | None = None,
    on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> str:
    """
    Run every (scenario, model, repeat) combination, appending each raw
    run_scenario() result as one JSON line to output_path.

    repeats defaults to 3 — fewer than that and you can't tell a real gap
    between two models apart from ordinary sampling noise; 3+ runs per
    (scenario, model) cell is the minimum to see the difference.

    Writes one JSON object per line and flushes after every run, so a
    long campaign against slow local models survives a crash/interrupt
    with only the in-flight run lost, not the whole campaign. Returns the
    path to the JSONL file written (auto-generated under
    campaign_results/ if output_path is omitted) for chapter-4-style
    offline analysis (pandas.read_json(path, lines=True), jq, etc.).

    on_progress, if given, is called after every individual run as
    on_progress(done, total, result) — this is the single hook both the
    CLI script and the web UI job runner use to report progress, so
    neither has to duplicate this loop to observe it.
    """
    if output_path is None:
        os.makedirs(_CAMPAIGN_RESULTS_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        output_path = os.path.join(_CAMPAIGN_RESULTS_DIR, f"campaign-{ts}.jsonl")
    else:
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

    total = len(scenario_ids) * len(model_ids) * repeats
    done = 0
    with open(output_path, "a", encoding="utf-8") as f:
        for scenario_id in scenario_ids:
            for model_id in model_ids:
                for rep in range(repeats):
                    result = run_scenario(
                        scenario_id,
                        agent=agent,
                        agent_model_id=model_id,
                        judge_model_id=judge_model_id,
                    )
                    result["repeat"] = rep
                    f.write(json.dumps(result, default=str) + "\n")
                    f.flush()
                    done += 1
                    logger.info(
                        "run_campaign: %d/%d done (scenario=%s model=%s rep=%d)",
                        done, total, scenario_id, model_id, rep,
                    )
                    if on_progress is not None:
                        on_progress(done, total, result)
    return output_path


def _coerce_score(value: Any) -> float | None:
    """Best-effort float() coercion for a judge-reported score.

    The judge is prompted to return `score` as a JSON number, but an LLM's
    JSON isn't guaranteed to comply — it sometimes emits a quoted string
    (e.g. "6.5" instead of 6.5), which parses fine as JSON but isn't a
    number. Returns None (rather than raising) for anything float() can't
    handle, so the caller can exclude-and-count instead of crashing
    aggregate_results on a single malformed value in a large batch.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate a list of run_scenario() results into summary statistics for an
    evaluation campaign.

    Methodology: three categories of run are excluded from the numeric
    averages below, and counted separately instead of silently blending into
    the mean:

      - stub runs (agent_path == "stub" — the model under test was
        unreachable and the deterministic offline fallback _stub_agent
        answered instead). A stub doesn't diagnose anything, but
        keyword_score can still land in the 6-8/10 range because the stub's
        boilerplate remediation text overlaps generic troubleshooting
        vocabulary in several scenarios' keyword lists.
      - judge errors (llm_score.error == True — a rate limit, timeout, or
        truncated response made the judge return score:0). That 0 is not a
        real quality judgment; averaging it in would make an infrastructure
        failure look like the agent scored badly.
      - non-numeric judge scores (llm_score.score parses as JSON but not as
        a number, e.g. the judge returned "6.5" as a quoted string). Observed
        on ~72% of judge responses across a real 120-run campaign — common
        enough that silently crashing sum()/len() on a mixed str/float list,
        or silently coercing and hiding the exclusion, are both wrong.

    Counts of all three are reported so nothing is hidden from the aggregate.
    """
    def _mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 2) if xs else None

    stub_results = [r for r in results if r.get("agent_path") == "stub"]
    live_results = [r for r in results if r.get("agent_path") != "stub"]

    judge_error_results = [r for r in live_results if (r.get("llm_score") or {}).get("error")]
    judge_ok_results = [r for r in live_results if r.get("llm_score") and not r["llm_score"].get("error")]

    kw_scores = [r["keyword_score"]["score"] for r in live_results if r.get("keyword_score")]
    lc_scores = [
        r["keyword_score"]["length_controlled_score"] for r in live_results
        if r.get("keyword_score") and "length_controlled_score" in r["keyword_score"]
    ]

    llm_scores: list[float] = []
    non_numeric_score_count = 0
    for r in judge_ok_results:
        coerced = _coerce_score(r["llm_score"].get("score"))
        if coerced is None:
            non_numeric_score_count += 1
        else:
            llm_scores.append(coerced)

    return {
        "total_runs": len(results),
        "included_runs": len(live_results),
        "excluded_stub_runs": len(stub_results),
        "excluded_judge_error_runs": len(judge_error_results),
        "excluded_non_numeric_judge_score_runs": non_numeric_score_count,
        "scored_llm_runs": len(llm_scores),
        "mean_keyword_score": _mean(kw_scores),
        "mean_length_controlled_score": _mean(lc_scores),
        "mean_llm_score": _mean(llm_scores),
        "judge_model": JUDGE_MODEL,
    }


def _model_total_cost_usd(model_rows: list[dict[str, Any]], model_id: str) -> float:
    """Sum model_cost tokens across model_rows into a dollar figure, using
    llm_client.MODEL_REGISTRY as the single source of pricing truth (never
    re-hardcode per-model prices here — they'd drift from the registry)."""
    entry = llm_client.MODEL_REGISTRY.get(model_id, {})
    price_in = entry.get("price_in_per_mtok", 0.0)
    price_out = entry.get("price_out_per_mtok", 0.0)
    total = 0.0
    for r in model_rows:
        mc = r.get("model_cost") or {}
        total += (mc.get("input", 0) / 1_000_000) * price_in
        total += (mc.get("output", 0) / 1_000_000) * price_out
    return round(total, 6)


def _model_avg_latency_s(model_rows: list[dict[str, Any]]) -> float | None:
    if not model_rows:
        return None
    return round(sum(r.get("total_ms", 0) for r in model_rows) / len(model_rows) / 1000, 1)


def _report_conclusion_fr(model_stats: dict[str, dict[str, Any]], results: list[dict[str, Any]]) -> str:
    """Deterministic, French, 2-3 sentence conclusion from per-model stats.
    No LLM call — the report must stay fast, free, and reproducible."""
    scored = {m: s["judge_score"] for m, s in model_stats.items() if s["judge_score"] is not None}
    if not scored:
        return "Aucun score de juge exploitable sur ces runs — impossible de comparer les modèles sur cette base."

    best_model = max(scored, key=lambda m: scored[m])
    sentences = [
        f"Sur les scénarios évalués, **{best_model}** obtient le meilleur score du juge "
        f"({scored[best_model]:.2f}/10)."
    ]
    if len(scored) > 1:
        worst_model = min(scored, key=lambda m: scored[m])
        if worst_model != best_model:
            sentences[-1] = sentences[-1][:-1] + f", contre {scored[worst_model]:.2f}/10 pour {worst_model}."

    latencies = {m: s["avg_latency_s"] for m, s in model_stats.items() if s["avg_latency_s"] is not None}
    if len(latencies) > 1:
        fastest = min(latencies, key=lambda m: latencies[m])
        if fastest != best_model and fastest in scored:
            sentences.append(
                f"{fastest} est nettement plus rapide en moyenne ({latencies[fastest]}s par run) "
                f"mais avec un score juge plus bas ({scored[fastest]:.2f}/10)."
            )

    total_excluded = sum(1 for r in results if r.get("agent_path") == "stub")
    if total_excluded:
        sentences.append(
            f"{total_excluded} run(s) ont été exclus des moyennes (modèle injoignable ou délai dépassé) — "
            "voir le détail des exclusions ci-dessus avant d'interpréter ces chiffres comme définitifs."
        )
    return " ".join(sentences)


def generate_report_markdown(
    results: list[dict[str, Any]], title: str = "Rapport d'évaluation multi-modèle"
) -> str:
    """
    Build a human-readable Markdown summary from a list of run_scenario()
    results (typically all lines of one run_campaign() JSONL file).

    This is the single report generator shared by run_evaluation_cli.py and
    the web UI's /api/eval/run job — both call this function directly rather
    than reimplementing report formatting, so the two launch paths always
    produce an identical report shape from identical underlying data
    (run_campaign + aggregate_results).

    Sections: a per-model table (scores, latency, cost), a breakdown of
    excluded runs and why, and a short deterministic conclusion.
    """
    if not results:
        return f"# {title}\n\nAucun run à rapporter.\n"

    model_ids = sorted({r.get("agent_model_id", "?") for r in results})
    lines = [
        f"# {title}",
        "",
        f"**Date :** {time.strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Runs totaux :** {len(results)}  ",
        f"**Modèles comparés :** {', '.join(model_ids)}",
        "",
        "## Résumé par modèle",
        "",
        "| Modèle | Runs | Réussis (live) | Score mots-clés /10 | Score juge /10 | Latence moy. | Coût total |",
        "|---|---|---|---|---|---|---|",
    ]

    model_stats: dict[str, dict[str, Any]] = {}
    for model_id in model_ids:
        model_rows = [r for r in results if r.get("agent_model_id") == model_id]
        agg = aggregate_results(model_rows)
        avg_latency_s = _model_avg_latency_s(model_rows)
        total_cost = _model_total_cost_usd(model_rows, model_id)
        model_stats[model_id] = {
            "judge_score": agg["mean_llm_score"],
            "keyword_score": agg["mean_keyword_score"],
            "avg_latency_s": avg_latency_s,
            "total_cost_usd": total_cost,
        }
        kw = agg["mean_keyword_score"]
        js = agg["mean_llm_score"]
        lines.append(
            f"| {model_id} | {agg['total_runs']} | {agg['included_runs']}/{agg['total_runs']} | "
            f"{kw if kw is not None else '—'} | {js if js is not None else '—'} | "
            f"{avg_latency_s if avg_latency_s is not None else '—'}s | "
            f"${total_cost:.5f} |"
        )

    global_agg = aggregate_results(results)
    lines += [
        "",
        "## Runs exclus",
        "",
        f"- **{global_agg['excluded_stub_runs']}** run(s) exclu(s) — modèle injoignable ou délai "
        "dépassé (réponse de secours utilisée à la place)",
        f"- **{global_agg['excluded_judge_error_runs']}** run(s) exclu(s) — le juge (LLM évaluateur) "
        "a échoué (délai, erreur réseau, réponse tronquée)",
        f"- **{global_agg['excluded_non_numeric_judge_score_runs']}** score(s) de juge exclu(s) du "
        "calcul de moyenne (valeur non numérique renvoyée par le juge)",
    ]

    stub_rows = [r for r in results if r.get("agent_path") == "stub"]
    if stub_rows:
        by_model_scenario: dict[tuple[str, str], int] = {}
        for r in stub_rows:
            key = (r.get("agent_model_id", "?"), r.get("scenario_id", "?"))
            by_model_scenario[key] = by_model_scenario.get(key, 0) + 1
        lines += ["", "### Détail des exclusions (stub)", ""]
        for (m, s), n in sorted(by_model_scenario.items()):
            lines.append(f"- {m} / {s} : {n} run(s)")

    lines += ["", "## Conclusion", "", _report_conclusion_fr(model_stats, results)]
    return "\n".join(lines) + "\n"


def _invoke_agent_with_usage(
    agent: str, symptom: str, agent_model_id: str = "claude-haiku-4-5"
) -> tuple[str, dict[str, int], str, str | None]:
    """Resolve and invoke the agent. Returns (output, usage_tokens, agent_path, stub_reason).

    agent_path is "live" when a real model produced the output, or "stub" when
    the deterministic offline fallback (_stub_agent) was used because the
    model under test was unreachable or returned nothing. Stub runs must be
    excluded from campaign aggregates (see aggregate_results) since they
    don't diagnose anything but can still score deceptively well on keyword
    overlap.

    agent_model_id is only honored for agent="ai_command" — the orchestrator
    path (agent="orchestrator") always uses its own internal model selection.
    """
    if agent == "orchestrator":
        try:
            from pydantic_ai_orchestrator import run_orchestrator_structured  # type: ignore
            envelope = run_orchestrator_structured(symptom)
            return envelope.get("rendered", ""), envelope.get("usage") or {"input": 0, "output": 0}, "live", None
        except (ImportError, AttributeError, KeyError) as e:
            logger.warning("orchestrator unavailable: %s", e)
            reason = f"orchestrator unavailable: {e}"
            return f"[{reason}]\n" + _stub_agent(symptom), {"input": 0, "output": 0}, "stub", reason
    if agent == "ai_command":
        out, usage = _ai_command_sync(symptom, agent_model_id)
        if out:
            return out, usage, "live", None
        return (
            _stub_agent(symptom), {"input": 0, "output": 0}, "stub",
            f"ai_command ({agent_model_id}) returned no output (missing credentials, model unreachable, or empty response)",
        )
    return _stub_agent(symptom), {"input": 0, "output": 0}, "stub", f"unknown agent: {agent!r}"


# Per-model timeout override (seconds) for _ai_command_sync. Falls back to
# _DEFAULT_TIMEOUT_S for any model not listed here.
#
# phi3.5:3.8b needs more room than the other Ollama models: a real 120-run
# campaign (10 scenarios x 4 models x 3 repeats) at 300s still saw 5/30
# phi3.5 runs time out and fall back to the stub agent, while qwen2.5:3b and
# llama3.2:3b had zero timeouts at the same 300s on the same hardware.
#
# KNOWN LIMITATION — cve-001 x phi3.5:3.8b: this specific (scenario, model)
# cell has timed out twice in a row, at two different timeout ceilings
# (300s, then 450s after the bump above) — the second attempt's own
# elapsed time (452.1s) landed right at the new ceiling too. That's a
# reproducible pattern tied to this scenario's prompt, not sampling noise
# or a one-off infra hiccup (the other 3 scenarios that failed at 300s all
# succeeded live at 450s). Treat cve-001/phi3.5:3.8b as a structural gap in
# the local-model comparison data on this hardware — raising the timeout
# further is not expected to fix it, and it should be reported as an
# explicit exclusion in chapter-4 analysis rather than retried.
_DEFAULT_TIMEOUT_S = 300
_TIMEOUT_OVERRIDES_S: dict[str, int] = {
    "phi3.5:3.8b": 450,
}


def _ai_command_sync(prompt: str, model_id: str = "claude-haiku-4-5") -> tuple[str | None, dict[str, int]]:
    """Call the given model (Claude or a local Ollama model, via llm_client's
    registry) with a network-engineering system prompt. Returns (text, usage).
    """
    result = llm_client.query(
        model_id,
        prompt,
        system=(
            "You are a senior network engineer (CCIE/JNCIE-level). Diagnose the symptom "
            "and respond with: (1) ROOT CAUSE in one sentence, (2) EVIDENCE bullets, "
            "(3) REMEDIATION steps including exact CLI for Junos/EOS/FRR as relevant."
        ),
        max_tokens=600,
        # Local Ollama models on CPU can take 1-3 min for a full 600-token
        # diagnosis (vs seconds for the Claude API) — give them room rather
        # than falsely stubbing out a model that just needs more time.
        timeout_s=_TIMEOUT_OVERRIDES_S.get(model_id, _DEFAULT_TIMEOUT_S),
    )
    if result.error:
        logger.warning("ai_command (%s) failed: %s", model_id, result.error)
    return result.text, dict(result.tokens)


def _stub_agent(symptom: str) -> str:
    """Deterministic, keyword-rich fallback so the harness still produces useful output offline."""
    return (
        "ROOT CAUSE: Best-effort offline diagnosis (no LLM available).\n"
        f"EVIDENCE: Symptom received — {symptom[:200]}\n"
        "REMEDIATION: Verify peer reachability with ping, check interface status, "
        "compare local and remote BGP/OSPF area/AS configuration, align MTU, restart neighbor "
        "(clear bgp neighbor) once root cause is confirmed."
    )
