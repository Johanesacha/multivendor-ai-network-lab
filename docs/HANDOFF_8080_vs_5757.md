# HANDOFF — `:8080` vs `:5757`: blank NetBox SoT panel & "LLM: offline"

> **Audience:** the next agent picking this up.
> **Status:** ✅ **RESOLVED (2026-06-03)** — Option 1 (scoped CORS allow-list) applied. The rest
> of this doc is preserved as the root-cause record; see the **Resolution** block below.
> **One-line:** Two separate front ends. The pretty UI (`:8080`) is a real static *bundle*
> (NOT a mock) that goes LIVE by fetching the backend (`:5757`). The browser *was* blocking
> that cross-origin fetch (**CORS, no `Access-Control-Allow-Origin` header**), so it silently
> fell back to **"Demo Mode" + "LLM: offline"**. The backend, LLM, and drift data were always
> **UP and healthy** — it was a display artifact, now fixed.

---

## ✅ Resolution (2026-06-03)

**Root cause (refined):** a prior security-hardening merge already added `flask_cors` with a
scoped `_CORS_ORIGINS` list — but that list only contained `:5757`'s own origins. `:8080` was
never whitelisted, so `flask_cors` emitted **no `Access-Control-Allow-Origin`** header for the
demo UI's reads → browser blocked them → "Demo Mode" fallback. It was a one-line allow-list
gap, **not** a missing feature and **not** a static/fake UI.

**Fix applied (Option 1):** `src/app.py` `_CORS_ORIGINS` default now includes the loopback
`:8080` origins:

```python
"http://localhost:5757,http://127.0.0.1:5757,"
"http://localhost:8080,http://127.0.0.1:8080"
```

Restarted via `launchctl kickstart -k gui/$(id -u)/com.geshlab.dcn-app`.

**Verified:**
- `Access-Control-Allow-Origin: http://localhost:8080` now returned on `/api/health`,
  `/api/llm/status`, `/api/mv/netbox-sot/*` (origin-scoped echo, **never `*`**).
- Hostile origins (`evil.example.com`, `localhost:9999`, `attacker.test`) still get **no** ACAO.
- A real browser `fetch()` from the live `:8080` page to `:5757` now succeeds
  (`/api/llm/status` 200 `available:true`; `/api/mv/netbox-sot/drift` 200 `drift_count:20`).
- Playwright screenshot of `:8080` shows the v4.0 UI **LIVE**: 26 DEVICES · 36 BGP UP ·
  15 ACTIVE ALERTS · device tree · EVPN topology · green **Live** pill (no "Demo Mode").

**Known remaining caveat (one panel):** the "Device Health Cards" panel POSTs
`/api/device/health-all`, which the `X-API-Key` gate 403s (the static `:8080` bundle carries
no key). That endpoint runs **read-only** `show`-type commands (`_HEALTH_CMDS`) but fans out
SSH to ~25 devices, so per the §9 guardrail ("paths that consume resources stay gated") it was
**left gated** pending an owner decision: (a) add it to `_OPEN_POST_PATHS` like the AEGIS
preflight sim routes, or (b) keep gated and have the panel degrade gracefully
("Live console required"). Not yet decided.

**Stale line-numbers corrected** (see §6): `/api/llm/provider` is **POST-only** at
`app.py:8493` (not GET/POST @8487); `/api/llm/status` @8507 (not 8501); `/api/llm/toggle`
@8595 (not 8589).

> §4c "smoking gun", §7 checklist ("confirm NO ACAO header"), and §8 "decision required"
> below describe the **pre-fix** state and are retained only as the historical root-cause trail.

---

## 1. TL;DR for a busy reader

- **Nothing is down.** `curl` reaches health, NetBox drift, and LLM status on `:5757` fine.
- The polished **"AI Network Tool v4.0"** UI only exists on **`:8080`** as a **static file**.
- It probes `:5757` on load to flip to LIVE. The browser blocks that read because the
  Flask backend returns **no CORS header** for the `:8080` origin.
- Result: `LIVE=false` → "Demo Mode" pill, blank NetBox SoT panel, and the literal
  fallback string **"LLM: offline"** — even though Claude Haiku + local Ollama are ready.
- `:5757` *is* the live app, but it serves a **different, older** console
  ("DCN Network Tool", CLI/Collect/NAPALM tabs) — not the v4.0 skin. That's why opening
  `:5757` "looked like the backend."

---

## 2. The two surfaces (do not confuse them)

| Port | launchd label | Process | What it is | What it serves |
|---|---|---|---|---|
| **`:8080`** | `com.geshlab.demo-ui` | `python -m http.server 8080 --directory .../DCN_Network_Tool/demo` | **Static file server** over the `demo/` folder | The polished **"AI Network Tool v4.0 — Multivendor Operations"** skin: Fleet view, NOC Wall, NetBox SoT, Health Gate, Auto-Remediation panels. **No backend of its own** — it's HTML/JS/CSS + bundled demo data. |
| **`:5757`** | `com.geshlab.dcn-app` | `venv_lab/bin/python app.py` (cwd `src/`), bound `127.0.0.1` | **Real Flask app** | The **live REST API** *and* its own **older "DCN Network Tool"** operator console (tabs: CLI / Collect / Capacity / Analysis / Reports / Topology / NAPALM / History). Device clicks do real SSH/collect. |

**Critical mental model:** the v4.0 skin and the live API are **two different programs on
two different origins**. They are NOT the same app on two ports. The nice UI cannot read
the live API across origins without CORS.

---

## 3. Root cause (confirmed, mechanism-level)

1. `demo/index.html` declares the live config:
   ```js
   const API = 'http://localhost:5757';
   let LIVE = false;
   ```
2. On load it calls `apiFetch(path)` which does `fetch(API + path, ...)` inside a
   `try/catch` with an `AbortController` timeout. **Any** failure (including a CORS-blocked
   read) returns `{ok:false, data:null}` — it cannot tell "server down" from "CORS blocked".
3. `setLive(live)` then sets the global `LIVE`. On failure it renders **"Demo Mode"** and
   `bar-mode` = *"localhost:5757 unreachable — showing demo data"*.
4. Panels gate on `LIVE`:
   - NetBox SoT / Health Gate render *"requires LIVE mode (backend on localhost:5757)"*.
5. Separately, the LLM status fetch (`/api/llm/status`) is also CORS-blocked → the pill
   prints its fallback literal **"LLM: offline"**.

**Why the browser blocks it:** the Flask backend responds **200 but with no
`Access-Control-Allow-Origin` header**. A page served from `http://localhost:8080`
performing `fetch('http://localhost:5757/...')` is a cross-origin request; without ACAO
the browser discards the response and the JS sees a network error. `curl` has no
same-origin policy, so it succeeds — which is why the services *look* up from the shell
but *dead* from the UI.

---

## 4. Evidence — reproduce it yourself

### 4a. Services & ports
```bash
launchctl list | grep -i geshlab
# expect: dcn-app, demo-ui, dcn-intel, clab-collector, napalm, netlog, portal

lsof -nP -iTCP:5757 -sTCP:LISTEN   # Flask, 127.0.0.1:5757
lsof -nP -iTCP:8080 -sTCP:LISTEN   # http.server, *:8080
lsof -nP -iTCP:11434 -sTCP:LISTEN  # ollama (local LLM), 127.0.0.1:11434
```

### 4b. Backend is healthy (curl ignores CORS)
```bash
B=http://127.0.0.1:5757
curl -s -o /dev/null -w "%{http_code}\n" $B/api/health                  # 200
curl -s $B/api/mv/netbox-sot/devices | head -c 200                      # 200, 26 devices, "mode":"simulated"
curl -s $B/api/mv/netbox-sot/drift   | head -c 200                      # 200, "drift_count":20
curl -s $B/api/llm/status            | head -c 250                      # available:true, provider:"claude", model:"claude-haiku-4-5-20251001"
```
Observed drift examples: `uk-lon-fw-02` presence missing (**high**); `de-fra-core-01`
IP `10.200.0.11` vs SoT `10.200.0.99` (**high**); `de-fra-core-02` model mismatch.

### 4c. The smoking gun — CORS header is absent for the `:8080` origin
```bash
for p in /api/llm/status /api/mv/netbox-sot/drift; do
  echo "--- $p ---"
  curl -s -D - -o /dev/null -H "Origin: http://localhost:8080" http://127.0.0.1:5757$p \
    | grep -i "access-control-allow-origin" || echo "NO ACAO header  <-- browser blocks the read"
done
```
**Result:** both routes return **NO `Access-Control-Allow-Origin`** → confirmed cause.

### 4d. Confirm it in the browser (optional)
Open `http://localhost:8080`, DevTools → Console. You'll see CORS errors like
`Access to fetch at 'http://localhost:5757/...' from origin 'http://localhost:8080' has
been blocked by CORS policy`. Network tab shows the requests as failed/CORS, not 200.

---

## 5. What is NOT a bug (don't "fix" these)

- **`mode: simulated`** — correct. Drift is computed against the **containerlab digital
  twin**, not a production NetBox. The panel still renders fully when LIVE. Do not treat
  "simulated" as an error.
- **`:5757` showing the old "DCN Network Tool" UI** — correct. That's the live app's own
  console. The v4.0 look is a *separate* static build under `demo/`.
- **`/api/drift` → 404** — correct. That path doesn't exist. The real routes are
  `/api/mv/netbox-sot/drift` (GET) and `/api/config-drift` (POST).

---

## 6. Code map (paths under `DCN_Network_Tool/`)

### Static demo (`:8080`) — `demo/index.html`
| Concern | Approx. line |
|---|---|
| `const API='http://localhost:5757'; let LIVE=false;` | ~3155 |
| `apiFetch()` (fetch + AbortController + catch→`{ok:false}`) | ~3162–3180 |
| `setLive()` (LIVE pill vs "Demo Mode" / "unreachable") | ~3181–3200 |
| LLM pill render + `'LLM: offline'` fallback | ~3455–3465 |
| `__llmStatus`, provider switch UI | ~3457–3524 |
| Health Gate `if(!LIVE){ ...requires LIVE mode... }` | ~5735–5745 |
| NetBox SoT "requires LIVE mode" gate | ~5743 / ~5891 |
| Preview bar "Connecting to localhost:5757…" | ~1247 |

### Live backend (`:5757`)
| Route | File:line |
|---|---|
| `GET /api/mv/netbox-sot/devices` | `src/multivendor_extensions.py:1698` |
| `GET /api/mv/netbox-sot/drift` | `src/multivendor_extensions.py:1712` |
| `POST /api/mv/netbox-sot/refresh` | `src/multivendor_extensions.py:1718` |
| `POST /api/mv/remediation/propose-for-drift` | `src/multivendor_extensions.py:1748` |
| `GET/POST /api/llm/provider` | `src/app.py:8487` |
| `GET /api/llm/status` | `src/app.py:8501` |
| `POST /api/llm/toggle` | `src/app.py:8589` |
| `POST /api/config-drift` | `src/app.py:4039` |

### launchd
- `~/Library/LaunchAgents/com.geshlab.demo-ui.plist` — serves `demo/` on 8080
- `~/Library/LaunchAgents/com.geshlab.dcn-app.plist` — runs `app.py` on 5757

---

## 7. Verification checklist for the next agent (do this first, before changing anything)

- [ ] `launchctl list | grep geshlab` → all 7 agents present
- [ ] `:5757/api/health` → 200
- [ ] `:5757/api/mv/netbox-sot/drift` → 200 with `drift_count` > 0
- [ ] `:5757/api/llm/status` → `available:true`
- [ ] ollama on `:11434` listening
- [ ] CORS test (§4c) → confirm **no ACAO header** (this is the cause)
- [ ] Browser console on `:8080` → CORS errors present
- [ ] Confirm whether the **public repo** ships the `demo/` folder, and whether the
      intended UX is "shareable canned mock" (Demo Mode by design) vs "local live console"

---

## 8. Fix options (decision required — pick one)

### Option 1 — Scoped CORS allow for `localhost:8080` (makes the v4.0 demo go LIVE)
Add a tight `after_request` header on the Flask app, **scoped to the loopback dev origin
only** (never `*`):
```python
# src/app.py — near app init / after_request hooks
@app.after_request
def _dev_cors(resp):
    origin = request.headers.get("Origin", "")
    if origin in ("http://localhost:8080", "http://127.0.0.1:8080"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp
```
- ✅ The v4.0 demo flips to 🟢 LIVE; NetBox SoT + LLM pills light up.
- ⚠️ Touches the security-hardened, fail-closed, loopback-bound backend. Keep it
  **origin-scoped to localhost** — this is local-dev only, must not ship to any
  exposed/public deployment.
- ⚠️ Mind the `X-API-Key` gate: if mutating routes require the key, the browser also needs
  a preflight (`OPTIONS`) allowance — the snippet covers headers/methods; verify the gate's
  `before_request` doesn't 401 the `OPTIONS` preflight.

### Option 2 — Serve `demo/index.html` from Flask itself (same-origin, no CORS)
Add a route on `:5757` that serves the `demo/` build (or copy it into the Flask static dir),
so the polished UI loads from the same origin as the API. No CORS needed, no new
cross-origin security surface. Cleanest if you want one URL.
- ⚠️ Two UIs then coexist on `:5757` (old console + v4.0). Decide routing (`/` vs `/v4`).

### Option 3 — Do nothing (Demo Mode is intentional)
If `demo/` is meant to be a **backend-free, shareable, canned mock** (for screenshots,
LinkedIn video, public repo), then "Demo Mode" + "LLM: offline" are **correct by design**
when no same-origin backend is present. Use `:5757`'s own console for live work.

**Recommendation:** Option 1 if the goal is "make the pretty UI show live data locally for
demos/screenshots"; Option 3 if `:8080` is purely a shareable artifact.

---

## 9. Guardrails (must hold for any fix)

- **Never** set `Access-Control-Allow-Origin: *`. Scope to `http://localhost:8080` (and
  `127.0.0.1:8080`) only.
- Do **not** disturb: the `X-API-Key` gate, the loopback bind, or the
  `MVLAB_API_KEY` (Flask app gate) vs `DCN_API_KEY` (separate stdio MCP process) split —
  never cross-wire them.
- `.env` stays gitignored; **no secrets** into the public repo.
- Keep AEGIS wiring out of the public repo.
- This is local-dev convenience only — CORS additions must not reach a publicly reachable
  bind.

---

## 10. Open questions to resolve

1. Is `demo/` shipped in the **public** `gesh75/multivendor-ai-network-lab` repo? If yes,
   any CORS/live wiring must be gated so it never assumes a reachable backend for outside
   cloners (their `:8080` should stay clean Demo Mode).
2. Should the v4.0 skin eventually **replace** the old `:5757` console, or stay a separate
   demo artifact? That decides Option 1 vs Option 2 long-term.
3. Does the `X-API-Key` `before_request` short-circuit `OPTIONS` preflights? Verify before
   enabling CORS on mutating routes.

---

_Generated during the Phase 6 launch session. Backend/LLM/drift all verified UP at time of
writing; the only defect is the cross-origin display fallback._
