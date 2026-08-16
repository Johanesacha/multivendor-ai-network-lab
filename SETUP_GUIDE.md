# Setup Guide — Windows dev machine

This guide documents a full setup + verification pass of **Lab A** (the 13-container
FRR backbone under `network-lab/`) plus the Flask/AI ops tool in `src/`, done on a
**Dell Latitude 5400, Intel Core i5-8365U (4 cores / 8 threads), 16 GB RAM, Windows 11**.

It assumes you know nothing else about this repo. Every command below was actually run
on this exact machine during the pass that produced this guide — nothing is copied
from the top-level README without re-verifying it here.

> Scope: this covers **Lab A only** (`network-lab/` — 10 FRR routers + InfluxDB +
> Grafana + telemetry collector = 13 containers) and the Flask API/UI in `src/`.
> **Lab B** (the `containerlab-multivendor/` CLOS EVPN fabric with Nokia SR Linux +
> Arista cEOS) is a separate, heavier setup that needs a non-public Arista cEOS image
> and was **not** touched in this pass — see `docs/BUILD_YOUR_OWN_LAB.md` if you need it.

---

## 0. Prerequisites

| Need | Why | How to check |
|---|---|---|
| Docker Desktop, running | runs all 13 containers | `docker info` should print a server version, not an error |
| git | clone + commit | `git --version` |
| Python 3.10+ | runs the Flask app in `src/` | see the **Windows Python gotcha** below |

### Windows Python gotcha

On a fresh Windows install, `python3` is very likely the **Microsoft Store app-execution
alias stub**, not a real interpreter. Symptom:

```
$ python3 --version
Python was not found; run without arguments to install from the Microsoft Store, or
disable this shortcut from Settings > Apps > Advanced app settings > App execution aliases.
```

`python --version` may *also* print a real version even when `python3` is the broken
stub — the two are not equivalent on Windows. **Use `python` or `py -3`, not `python3`**,
for every command below (the top-level README says `python3`; that's correct for
macOS/Linux, not Windows). Verify before proceeding:

```bash
python --version        # or: py -3 --version
python -c "print('ok')" # must actually print ok, not the Store-alias error
```

### Docker Desktop resource check

```bash
docker info --format '{{.OSType}}/{{.Architecture}} - NCPU {{.NCPU}} - MemTotal {{.MemTotal}}'
```

On this machine: `linux/x86_64 - NCPU 8 - MemTotal ~7.7GB`. Docker Desktop's WSL2 VM
doesn't automatically get all 16 GB of host RAM — if `MemTotal` looks low, raise the
limit in Docker Desktop → Settings → Resources.

---

## 1. Clone and branch

```bash
git clone https://github.com/<your-fork>/multivendor-ai-network-lab.git
cd multivendor-ai-network-lab
```

Make sure you're on a branch that actually contains **both** the full application
(`src/app.py`, `docker-compose.yml`, the demo UI, etc.) **and** the `network-lab/`
fixes described below — some branches in this repo's history only contain one or the
other (see **Appendix: branch history note** at the end if you're ever unsure which
branch you're on).

---

## 2. `.env` setup

There are **two** `.env.example` files in this repo — they are for different, unrelated
subsystems:

- **`.env.example` (repo root)** → copy to **`src/.env`**. This is the one that matters
  for everything in this guide (`MVLAB_API_KEY`, `CLI_PROXY_PASSWORD`, `ANTHROPIC_API_KEY`,
  SSH mode, etc.). `src/app.py` calls `load_dotenv()` with no arguments, which walks up
  from `src/` looking for `.env` — so the file must be named `src/.env`, not `.env` at
  the repo root.
- **`src/.env.example`** → an older/legacy template (LibreNMS, Docker Model Runner,
  JMCP) for a different, optional local-LLM report-narrative feature. You can ignore
  it unless you specifically want that feature (see the LLM Judge vs. local LLM Runner
  note in §7).

```bash
cp .env.example src/.env
```

Now edit `src/.env`:

1. **Generate the two required secrets** (the app fails closed — HTTP 503 — on
   mutating endpoints if these are unset):

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"   # → MVLAB_API_KEY
   python -c "import secrets; print(secrets.token_urlsafe(24))"   # → CLI_PROXY_PASSWORD
   ```

   Paste the two values into `MVLAB_API_KEY=` and `CLI_PROXY_PASSWORD=` in `src/.env`.

2. **Set `DCN_SSH_MODE=key`** (not the default `pkcs11`). `pkcs11` mode expects a
   YubiKey + PyKCS11 + an OpenSC PKCS#11 library — none of which exist on a normal dev
   laptop. `key` mode uses the SSH keypair you generate in §3
   (`network-lab/ssh-keys/lab_key`), which `app.py` already defaults to.

3. **Set `FRR_DEFAULT_PASSWORD=change-me-in-prod`** — matches the root password baked
   into `network-lab/Dockerfile`, used as an SSH fallback if key auth doesn't apply.

4. **`ANTHROPIC_API_KEY`** — see §4.

Everything else in `src/.env` can stay at its template default for a local lab run.

---

## 3. Generate the lab SSH keypair

`network-lab/Dockerfile` bakes `network-lab/ssh-keys/lab_key.pub` into every router
image's `authorized_keys` at **build time** — this directory doesn't exist until you
create it, and it's gitignored (never committed) on purpose.

```bash
mkdir -p network-lab/ssh-keys
ssh-keygen -t ed25519 -f network-lab/ssh-keys/lab_key -N "" -C "network-lab"
```

`app.py`'s FRR SSH key path already defaults to this exact location
(`network-lab/ssh-keys/lab_key`) — no env var needed unless you move it.

---

## 4. `ANTHROPIC_API_KEY` — step by step

1. Get a key from the Anthropic Console (`sk-ant-api03-...`).
2. Open `src/.env`, find the commented-out line:
   ```
   # ANTHROPIC_API_KEY=     # Claude fallback — load from a secrets manager or ~/.env
   ```
3. Uncomment it and paste your key: `ANTHROPIC_API_KEY=sk-ant-api03-...`
4. **Never commit this file.** `src/.env` is gitignored — double-check with
   `git check-ignore -v src/.env` before your first commit if you're paranoid (it should
   print a match, not empty output).

> ⚠️ **If you ever paste your key into a chat tool, terminal share, or ticket**, treat
> it as compromised and rotate it in the Anthropic Console — plaintext API keys that
> pass through any logged channel should not be trusted long-term, regardless of where
> that channel is.

This key powers two **separate** things — don't confuse them (see §7 for the third,
unrelated "LLM: offline" badge in the UI):

- **`/api/mv/orchestrator`** — the AI Assistant / Agent Coordinator (diagnosis agent).
- **Eval Harness LLM-as-judge** — scores each scenario run 0–10 via `claude-haiku-4-5`
  in addition to the deterministic keyword score.

Without a key, both silently degrade to offline/heuristic mode — nothing crashes, you
just get worse answers and `llm_score` stays absent.

---

## 5. Build and launch the stack (13 containers)

First, create `network-lab/.env` (a **separate** file from `src/.env` — docker compose
only auto-loads a `.env` from the same directory as the compose file it's running).
This carries the password for the in-container HTTP CLI proxy used by the "CLI
Transport" UI panel — without it, `cli_proxy.py` refuses to start inside every router
(fail-closed by design) and that panel always shows 0/10 devices:

```bash
cd network-lab
echo "CLI_PROXY_PASSWORD=$(grep ^CLI_PROXY_PASSWORD= ../src/.env | cut -d= -f2-)" > .env
docker compose build
docker compose up -d
```

Expect **10 FRR routers + InfluxDB + Grafana + `frr-telemetry`** = **13 containers**.

Verify all are up and check for restarts (a restarting container means something's
crash-looping):

```bash
docker compose ps -a --format "table {{.Name}}\t{{.Status}}"
docker inspect --format='{{.Name}}: restarts={{.RestartCount}}' $(docker compose ps -q)
```

All 13 should read `Up ...` with `restarts=0`. If a router shows
`Restarting (255)` and its logs (`docker logs <name>`) say
`exec /entrypoint.sh: no such file or directory`, see **Known bug #1** below — it's
already fixed in this branch, but if you ever see it again after a `git pull` or a
fresh clone elsewhere, it means `.gitattributes` didn't apply (see the fix for how to
force it).

### BGP / OSPF / BFD convergence check

```bash
docker exec de-fra-core-01 vtysh -c 'show bgp summary'
docker exec de-fra-core-01 vtysh -c 'show ip ospf neighbor'
docker exec de-fra-core-01 vtysh -c 'show bfd peers brief'
```

Expected on a healthy lab: 6 BGP neighbors **Established**, 9 OSPF neighbors in
**2-Way** or **Full** state (only DR/BDR go Full on this broadcast segment — that's
normal, not a bug), and 9 BFD sessions **up**.

You'll see one harmless line on every `vtysh` call:
`% Can't open configuration file /etc/frr/vtysh.conf due to 'No such file or directory'.`
— cosmetic only, `vtysh` still works. Ignore it.

### Cold-start stability — the signal-11 crash from the previous (2-vCPU) machine

The commit history in this repo (`fix(lab): stagger FRR daemon startup...`) documents a
real signal-11 (segfault) crash-loop caused by CPU starvation when all 10 routers'
FRR daemons (zebra/bgpd/ospfd/staticd/bfdd/watchfrr = 60 processes) start at once on a
**2-vCPU** host. **On this 4-core/8-thread machine it did not reproduce**: all 10
routers came up clean and stayed at `restarts=0` through a 6+ minute observation
window, well past the daemons' 0–27s stagger window. The stagger logic itself is still
in `entrypoint.sh` and still runs (harmless — worst case it adds a few seconds to
startup) — it just isn't load-bearing on hardware this size.

---

## 6. Start the Flask API + UI

```bash
cd ../src
py -3 -m venv venv        # or: python -m venv venv — NOT python3, see §0
source venv/Scripts/activate     # Windows Git-Bash path (venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
python app.py
```

Watch the startup log for these two lines specifically:

```
[startup] FRR SSH key: OK (...\network-lab\ssh-keys\lab_key)
[startup] network-lab: OK (...\network-lab)
```

If either says something other than `OK`, go back to §3 — the key path or working
directory is wrong. Then:

```
* Running on http://127.0.0.1:5757
```

Open **http://127.0.0.1:5757/demo/index.html**.

You'll also see harmless startup warnings you can ignore for a lab run: missing
SecureCRT/NetBox CSV inventory files (hardcoded default paths from the original
author's machine — the FRR lab devices load fine regardless), `NAPALM: NOT installed`
(NAPALM isn't in `requirements.txt` — only needed for non-FRR device types this lab
doesn't exercise), and `[AEGIS] Preflight not available` (an optional module, not in
`requirements.txt`, not needed here).

---

## 7. The demo UI **needs the API key too** — a separate step from `.env`

This is the single most common "why doesn't anything work" moment: **opening the UI in
a browser does not give it your `MVLAB_API_KEY`.** Every mutating `POST` (Agent Chat,
Eval Harness run, Chaos Monkey break/fix) is gated behind the `X-API-Key` header, and
the UI reads that key from **browser `localStorage`**, not from `src/.env` (the browser
has no access to your server-side `.env` file — that would be a security hole, not a
bug). Symptom if you skip this: `POST /api/chat` (or `/api/mv/eval/run`, etc.) returns
**403** and the Agent Chat panel just hangs.

Open the browser devtools console on `http://127.0.0.1:5757/demo/index.html` and run:

```js
localStorage.setItem('MVLAB_API_KEY', '<same value as MVLAB_API_KEY in src/.env>')
```

Then reload the page. This is a one-time step per browser profile.

### The "LLM: offline" badge top-right is not about your Anthropic key

The UI header shows an `LLM: offline` badge. This refers to a **third, separate,
fully optional** feature — a local Docker Model Runner / Ollama-style model
(`gemma4:latest`) used only for extra narrative text in some reports. It is unrelated
to `ANTHROPIC_API_KEY` and unrelated to the orchestrator/eval-harness LLM path in §4.
`GET /api/llm/status` will confirm: `{"available":false,"enabled":true,"hint":"Enable
Docker Model Runner..."}`. Safe to ignore unless you specifically want that feature.

---

## 8. Tour of the panels (all verified working this pass)

Top nav: **All / Observe / Diagnose / Operate / Audit**, plus a command palette
(`Cmd/Ctrl+K` — useful when the left sidebar's current section doesn't show the item
you want; e.g. GAIT Audit is reachable by typing "GAIT" into the palette even when the
sidebar is scrolled to a different section).

- **Home / Health** (Overview): fleet tiles — devices, sites, vendors, BGP-up count,
  active alerts. Confirmed live counts (26 devices, 5 sites, 36 BGP sessions up) after
  the FRR lab came up.
- **Diagnose → AI Assistant / Agent Coordinator**: free-text chat routed to the right
  agent (routing/remediation/verification/etc.). Requires the `localStorage` step
  above. Verified end-to-end with a real Claude call (real token usage in the
  response, not a canned string).
- **Diagnose → Eval Harness**: pick 1 of 10 incident scenarios, click Run. Returns a
  keyword score (deterministic) and an LLM-judge score (needs `ANTHROPIC_API_KEY`).
- **Diagnose → Chaos Monkey (BGP)**: "Show BGP Status" / "Break BGP Session" /
  "Fix All Sessions" / "Random Chaos" — genuinely flaps real BGP sessions on the lab
  routers (see §9 Bug 4 for the Windows-specific fix that made this work).
- **Audit → GAIT Audit**: immutable JSONL audit trail of every AI action, with token
  cost. Fully verified — every orchestrator call and eval run in this pass showed up
  here immediately with matching token counts (`GET /api/mv/gait/stats` matched the UI
  exactly: 5 events, 4791 input / 4169 output tokens after our test run).
- **Audit → Inventory**: 41-device table (Juniper/Arista sanitized configs + 13 live
  FRR entries) with hostname/site/vendor/role/mode/model, filterable.

---

## 9. Known bugs found on this machine

### Bug 1 — FRR routers crash-loop on fresh Windows checkout (`exec /entrypoint.sh: no such file or directory`) — **fixed in this branch**

**Cause:** Windows git defaults to `core.autocrlf=true`, which checks text files out
with CRLF line endings. `network-lab/entrypoint.sh`'s shebang becomes `#!/bin/bash\r`,
and the kernel can't resolve `/bin/bash\r` as an interpreter inside the Linux
container — Docker reports it as "no such file or directory" for the entrypoint
itself. All 10 routers hit this and crash-loop (`Restarting (255)`) forever.

**Fix:** added `.gitattributes` at the repo root forcing `eol=lf` for `*.sh` (and
`entrypoint.sh`, `Dockerfile` explicitly). Committed as
`fix(lab): pin influxdb/grafana/frr-telemetry to linux/amd64` (bundled with the
platform fix below — same session).

**If you ever hit this again** (e.g. after `git pull` on a repo checked out *before*
`.gitattributes` existed): `git add --renormalize .` then rebuild
(`docker compose build && docker compose up -d`).

### Bug 2 — `influxdb`/`grafana`/`frr-telemetry` pinned to `linux/arm64` — **fixed in this branch**

Same root cause class as the already-fixed FRR router image (arm64→amd64). Left
unfixed for these three services in `network-lab/docker-compose.yml`. On an amd64 host
this forces QEMU emulation — directly observed as `frr-telemetry`'s pip install pulling
`aarch64` wheels and taking 145s instead of a few seconds. Fixed by pinning all three
to `platform: linux/amd64`.

### Bug 3 — Eval Harness LLM-judge intermittently returns `score: 0, error: true` — **fixed**

Was reproducible on longer agent outputs (e.g. the verbose "ROUTING AGENT" structured
format from the `orchestrator` agent): the judge was called with `max_tokens=400`, and
its own reasoning text could run long enough to hit the cap *before* emitting the
closing `}`, so the regex match in `llm_judge()` failed and returned
`{"score": 0, "error": true}` even when the underlying diagnosis was good.

**Fix (already in this branch):** `src/eval_harness.py`'s `llm_judge()` now calls with
`max_tokens=1000` and explicitly instructs the judge to answer with strict single-line
JSON only (no prose before/after). Verified with a 4-run CLI regression
(`run_evaluation_cli.py --scenarios bgp-001,ospf-001 --models claude-haiku-4-5,qwen2.5:3b --repeats 1`)
— zero judge failures, real scores for both models on both scenarios.

### Bug 4 — Chaos Monkey ("Break BGP Session" etc.) did not actually touch the routers on Windows — **fixed**

Root cause was two independent problems stacked on top of each other in
`subprocess.run(["bash", <path>, action])` (used by both `/api/chaos/bgp` and
`/api/remediate`):

1. Passing a Windows-style absolute path (`C:\...`) as a bash argv entry gets mangled
   by Git Bash's MSYS path-translation layer.
2. More fundamentally: the bare string `"bash"` doesn't reliably resolve to Git Bash on
   Windows. Win32's `CreateProcess` resolves an unqualified executable name via its own
   search order (app dir → current dir → **System32** → Windows dir → *then* `PATH`),
   and `C:\Windows\System32\bash.exe` — Microsoft's WSL launcher stub — sits in that
   search path ahead of `PATH` entirely. So even with Git Bash earlier in `PATH`, this
   call was silently launching **WSL's** bash instead. WSL mounts Windows drives at
   `/mnt/c/`, not `/c/`, so every script path failed with "No such file or directory"
   regardless of the backslash-mangling fix alone.

**Fix (already in this branch):** `src/app.py` now resolves the real Git Bash
executable via `shutil.which("bash")` (a proper `PATH`-ordered lookup, cached after the
first call) instead of the bare string `"bash"`, combined with converting the script
path to the POSIX form Git Bash expects. Verified by clicking "Break BGP Session" then
"Fix All Sessions" and cross-checking `vtysh -c 'show bgp summary'` before/after: the
targeted session's `Up/Down` timer visibly reset at the moment of the click, confirming
the action now genuinely flaps the real BGP session.

### Bug 5 — CLI Transport panel always showed 0/10 devices — **fixed**

`network-lab/cli_proxy.py` (the HTTP CLI proxy running inside each of the 10 router
containers) refused to start: `docker-compose.yml` never passed it a
`CLI_PROXY_PASSWORD`, and even once it had one, the proxy defaults to binding
`127.0.0.1` inside the container — unreachable via Docker's published port mapping,
which forwards to the container's bridge interface, not its loopback.

**Fix (already in this branch):** added `CLI_PROXY_PASSWORD` and `CLI_PROXY_HOST=0.0.0.0`
to all 10 router services' `environment:` blocks in `network-lab/docker-compose.yml`.
**You still need one extra step this guide didn't cover before:** create
`network-lab/.env` (docker compose only auto-loads a `.env` from the same directory as
the compose file it's running — not `src/.env`) with the **same** `CLI_PROXY_PASSWORD`
value as `src/.env`:

```bash
echo "CLI_PROXY_PASSWORD=$(grep ^CLI_PROXY_PASSWORD= src/.env | cut -d= -f2-)" > network-lab/.env
cd network-lab && docker compose up -d --force-recreate
```

(There's a `network-lab/.env.example` documenting this — copy/adjust if you'd rather
generate a fresh password instead of reusing `src/.env`'s.) Verified:
`curl -u admin:$CLI_PROXY_PASSWORD http://127.0.0.1:8801/exec/show%20bgp%20summary`
returns real `vtysh` output, and the UI's "Collect All 10 via HTTP" button now shows
`10/10 devices`.

### Bug 6 — Nornir "BGP Health Check" flagged every healthy router as WARNING — **fixed**

`_nornir_worker()` classified any SSH output containing the substring `"down"` as
`status=warn`. The standard `show bgp summary` column header is literally **"Up/Down"**
— so every healthy BGP session on every vendor tripped this, regardless of actual
state.

**Fix (already in this branch):** switched to word-boundary regex matching, excluding
`"down"` when preceded by `/` (so the table header no longer matches while genuine
failure text like "interface down" still does). Verified: "BGP Health Check" on DE-FRA
now reports `4 OK / 0 WARNING` (all peers actually Established) instead of `4 WARNING`.

### Bug 7 — Fleet Audit always showed static demo data ("8 CRITICAL / 24 WARNINGS / 248 PASSED / 79 AVG SCORE"), never live — **fixed**

Two independent bugs stacked: (1) `network-lab/demo-devices/inventory.json`'s `config`
field used the full region-prefixed hostname (e.g. `eos/nl-ams-eos-rt-01.txt`) for all
16 static Juniper/Arista devices, but the actual files on disk are named without the
region prefix (e.g. `eos/ams-eos-rt-01.txt`) — every config lookup 404'd. (2) Even with
that fixed, `demo/index.html`'s `runFleetAudit()` checked for a `data.devices` /
`data.summary` response shape that `POST /api/mv/batfish/fleet` has never actually
returned (real shape: flat `results`/`total_errors`/`total_passes`/`fleet_score`) — so
it always silently fell back to the hardcoded demo dataset regardless of whether Flask
was reachable.

**Fix (already in this branch):** corrected all 16 filenames in `inventory.json`, and
updated `runFleetAudit()` to read the actual backend response shape. Verified: "Run
Audit" now shows live per-device findings (varies run to run — expect ~15-20 WARNINGS,
0 CRITICAL, score in the 90s — instead of the fixed 8/24/248/79 demo numbers).

### Bug 8 — NAPALM tab was 100% hardcoded, never called the real backend — **fixed**

`demo/index.html`'s NAPALM tab (Version Audit / BGP Status / Env Health / Iface
Errors) never made a single API call — unlike every sibling Operate tab (CLI, Collect,
Analysis), which all branch on live/demo state. A full working set of
`/api/napalm/*` endpoints already existed server-side.

**Fix (already in this branch):** wired all four buttons to the real endpoints (async
job-poll pattern: POST starts a job, `GET /api/napalm/jobs/<id>` polls it), falling
back to the old canned text only when not connected or the call fails outright.
Verified: "BGP Status" for DE-FRA now shows real per-router peer counts matching actual
lab state, labeled "(LIVE)".

### Bug 9 — localStorage API-key name mismatch between demo/index.html and src/app.js — **fixed**

`src/app.js` read the browser API key from `localStorage['mvlab_api_key']` (lowercase)
while `demo/index.html` used `localStorage['MVLAB_API_KEY']` (uppercase) — two
different keys under the same origin. Following one page's setup instructions didn't
carry over to the other page, surfacing as an unexplained 403 on the Eval Harness "Run"
button in `demo/index.html`. **Fix:** standardized on the uppercase form everywhere.
The one-time browser step in §7 above already uses the correct (now-consistent) name.

---

## 10. Known limitations — still true, not bugs

These are intentional/labeled design choices, not defects: **NAPALM/Deep-Analysis/Log/
Drift/Security-Audit "Analysis" sub-tabs** show fixed demo data with a visible `⚪ DEMO`
label when a real live call isn't warranted (heterogeneous Junos/EOS/FRR devices with
no real hardware behind the 16 static configs) — **Syslog/SNMP Traps** have a real UDP
receiver but synthetic injected content — **SuzieQ's "FRR" vendor filter** always
returns 0 results by design (it only parses the 16 static configs, not the live FRR
fleet, which has no config file in that format) — **AI Command / Doc Search** call a
local→Claude fallback cascade that's fast on this machine (Ollama installed) but was
slow (30-60s) on a machine without any local LLM service; a `_ping_ollama()`
short-circuit already mitigates the worst case.

---

## 11. Final checklist

- [ ] `docker info` succeeds, Docker Desktop running
- [ ] `python -c "print('ok')"` works (not `python3` — see §0)
- [ ] `src/.env` exists, `MVLAB_API_KEY` / `CLI_PROXY_PASSWORD` set, `DCN_SSH_MODE=key`,
      `ANTHROPIC_API_KEY` set
- [ ] `network-lab/ssh-keys/lab_key` + `.pub` exist
- [ ] `network-lab/.env` exists with the same `CLI_PROXY_PASSWORD` as `src/.env` (§9 Bug 5)
- [ ] `docker compose ps -a` shows 13/13 containers `Up`, `restarts=0` on all 10 routers
- [ ] `vtysh -c 'show bgp summary'` shows 6 Established neighbors on `de-fra-core-01`
- [ ] Flask app running, `http://127.0.0.1:5757/demo/index.html` loads
- [ ] `localStorage.MVLAB_API_KEY` set in the browser (§7) — Agent Chat responds
- [ ] Eval Harness run returns both a keyword score and an LLM-judge score
- [ ] GAIT Audit panel shows your test events with matching token counts
- [ ] Chaos Monkey Break/Fix genuinely flaps a real BGP session (cross-check with
      `vtysh -c 'show bgp summary'` before/after — Up/Down timer should reset)
- [ ] CLI Transport "Collect All 10 via HTTP" shows 10/10, not 0/10

If every box above is checked, the lab is ready to build on.

---

## Appendix: branch history note

At the start of this pass, the branch this repo was cloned on (`master`) contained
**only** `network-lab/` (Dockerfile, 10 routers' configs, `entrypoint.sh`) and
`src/requirements.txt` — an orphan branch with no shared git history with `main`, where
the actual application (`src/app.py`, the demo UI, `docker-compose.yml`, etc.) lives.
The six `fix(lab)`/`fix(deps)` commits already in this repo's history were made against
that orphan branch and, on their own, applied to nothing runnable. This pass
cherry-picked all six onto a branch based on `origin/main`, resolving the conflicts by
keeping `main`'s real device configs with the six commits' specific fixes applied on
top (verified line-by-line — every conflict was exactly the fix commit's stated
change, nothing else). If your `master`/`main` already contains both the app and these
fixes, this note is historical only and you can ignore it.
