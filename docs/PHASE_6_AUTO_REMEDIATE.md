# Phase 6 — Event-Initiated Remediation (Auto-Remediate)

Closes the last gap in the AI-SRE loop: a **detected anomaly auto-triggers the
existing closed-loop change pipeline**, risk-gated — no human button-press for
LOW-risk events, one-click approval for MEDIUM/HIGH, page-out only for CRIT.

```
anomaly ──match(auto.yaml)──▶ runbook ──risk_tier──▶ ┌ LOW          → execute now
 (/api/anomaly/detect                                ├ MEDIUM/HIGH  → queue → one-click approve
  ADTK z-score / flap)                               └ CRIT         → webhook page-out (never auto-acts)
                                                          │
                                                          ▼
                              config → POST /api/change/closed-loop (6-stage, auto-rollback)
                              exec/collect → device command path
```

## Files

| File | Role |
|---|---|
| `src/runbooks/auto.yaml` | Runbook catalog — 8 anomaly→remediation mappings + risk tiers (item **B**) |
| `src/auto_remediate.py` | Engine: load · normalize · match · fill · tier · decide · execute · queue · loop · blueprint (item **A** + **D**) |
| `src/tests/test_auto_remediate.py` | 14 unit + endpoint tests (`cd src && python tests/test_auto_remediate.py`) |
| `src/app.py` | Fail-safe registration block (`[AUTO-REMEDIATE] …` on boot) |

## Risk model

`risk_tier` in `auto.yaml` is a **floor**. The engine escalates it with Blast-Radius
BFS (`/api/batfish/blast-radius`): ≥3 downstream devices → +1 tier, ≥8 → +2, capped at CRIT.
It **never demotes** below the floor.

| Tier | Behavior |
|---|---|
| `LOW` | Auto-execute immediately |
| `MEDIUM` / `HIGH` | Queue `pending_approval` → one-click `approve`/`decline` |
| `CRIT` | GAIT entry + webhook page-out; **never auto-acts** |

**Safety:** a config runbook whose template has unresolved `{placeholders}` is parked
`needs_enrichment` and is **never fired half-filled**, regardless of tier.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/auto-remediate/status` | open | loop health, action counts by status |
| GET | `/api/auto-remediate/queue?limit=N` | open | recent + pending actions |
| GET | `/api/auto-remediate/runbook` | open | loaded catalog summary |
| POST | `/api/auto-remediate/approve/<id>` | **X-API-Key** | approve a queued MED/HIGH action → execute |
| POST | `/api/auto-remediate/decline/<id>` | **X-API-Key** | decline + GAIT reason |
| POST | `/api/auto-remediate/simulate` | **X-API-Key** + `DCN_AUTO_REMEDIATE_SIMULATE=1` | inject a synthetic anomaly (debug/demo) — **OFF by default** |

Mutating endpoints require the `X-API-Key` header (`DCN_API_KEY`), like the rest of `/api/*`.

## Security

* **`simulate` is gated off by default.** It accepts an arbitrary anomaly body, so the
  route is only registered when `DCN_AUTO_REMEDIATE_SIMULATE=1`, and it still sits behind
  the `X-API-Key` gate. Real operation never needs it — anomalies arrive from
  `/api/anomaly/detect`.
* **Placeholder injection guard.** Every `{placeholder}` value (`interface`, `peer_ip`,
  `asn`, `expected`, `host`, …) is strictly validated by `valid_field()` before substitution
  into any config/command payload — control characters, shell/config metacharacters, and
  malformed IPs are rejected. A failing value parks the action `rejected_invalid_field` and
  it is **never executed**, even from an untrusted source.

## Enable the background loop (opt-in)

Endpoints are always registered; the polling loop is **off by default**. Turn it on with:

```bash
# in DCN_Network_Tool/.env  (or the launchd plist EnvironmentVariables)
DCN_AUTO_REMEDIATE_S=300        # poll /api/anomaly/detect every 5 min
launchctl kickstart -k gui/$(id -u)/com.geshlab.dcn-app
# boot log -> [AUTO-REMEDIATE] 6 endpoints + loop ON (every 300s)
```

## Demo (no waiting)

```bash
KEY=$(grep ^DCN_API_KEY= .env | cut -d= -f2-)
# induce-style auto-fix via the simulate hook:
curl -s -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"detector":"flap","metric":"bgp_established","device":"de-fra-core-01"}' \
  http://localhost:5757/api/auto-remediate/simulate
# -> {"runbook":"bgp_flap_reset","risk_tier":"LOW","status":"auto_executed", ...}

# a MEDIUM that queues for approval:
curl -s -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"detector":"drift","metric":"mtu","device":"leaf1","interface":"eth2","expected":9000}' \
  http://localhost:5757/api/auto-remediate/simulate
# -> status "pending_approval"  ->  POST /api/auto-remediate/approve/<id>
```

## Status

- ✅ **A** auto-remediate engine + background loop
- ✅ **B** 8-runbook catalog (`auto.yaml`)
- ✅ **D** 6 API endpoints
- ✅ Tests 14/14 · live smoke verified (gated 403, LOW auto_executed, lab healthy)
- ⏳ **C** UI "Auto-Remediation Queue" tab (`demo/index.html`)
- ⏳ **E** 5 MCP tools (`mv_auto_*` → 68 total)

## Architecture note

The engine is dependency-injected (`auto_remediate.Deps`) so the pure logic unit-tests
with zero Flask/network/InfluxDB dependency; `app.py` injects the real implementations
(in-process HTTP to the closed-loop / anomaly / blast-radius endpoints with the API key,
`_agent_emit` for the agent log, `vendor_canonical` lookup from `DEVICES`).
