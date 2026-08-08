# Journal de test — UI complète (AI Network Tool v4.0)

Test exhaustif, panneau par panneau, de la sidebar `http://127.0.0.1:5757/demo/index.html`.
Fait sur le même Dell Latitude 5400 / Windows 11 que le [SETUP_GUIDE.md](SETUP_GUIDE.md),
stack complète lancée (13 conteneurs `network-lab/`), `ANTHROPIC_API_KEY` configurée,
`localStorage.MVLAB_API_KEY` réglée dans le navigateur (sinon tout POST renvoie 403 —
voir SETUP_GUIDE.md §7).

**Comment lire ce document** : pour chaque panneau — ce qu'il fait, une action concrète
que tu peux reproduire toi-même (bouton à cliquer ou requête à lancer), le résultat
exact obtenu chez moi, et les anomalies constatées. Les captures d'écran ne sont pas
jointes (session texte) — mais chaque résultat est donné texto (JSON, texte affiché...)
pour que tu puisses comparer avec ce que tu obtiens.

**Légende anomalies** : 🟢 fonctionne comme attendu · 🟡 fonctionne mais avec un
comportement surprenant (documenté) · 🔴 ne fonctionne pas / résultat incohérent.

**Sur "LIVE" vs "DEMO"** : ce lab mélange volontairement deux catégories de panneaux
(c'est un choix de conception du projet, pas un défaut à corriger) :
- **LIVE** : branché sur les 10 vrais routeurs FRR (`network-lab/`) via SSH/vtysh —
  les chiffres viennent d'une vraie commande exécutée sur un vrai conteneur au moment
  du clic.
- **DEMO / simulé** : illustre ce qu'un outil produirait dans une vraie entreprise
  (LibreNMS, Keep, Kibana, NetBox, Grafana...) — aucun de ces outils n'est réellement
  déployé dans ce lab, donc ces panneaux renvoient des données de démonstration
  cohérentes mais fixes/aléatoires, pas l'état réel de quoi que ce soit. Je précise
  pour chaque panneau lequel des deux il est.

---

## OVERVIEW

### Home / Health (`data-target="health"`)
**Catégorie : LIVE.** Vue d'ensemble de la flotte : tuiles Devices/Sites/Vendors/BGP
Up/Active Alerts, plus un bouton **▶ Fetch All Devices** qui déclenche
`POST /api/device/health-all` et affiche une carte par appareil (CPU/MEM/BGP/OSPF/Optics).

**Action testée** : clic sur "Fetch All Devices".
**Résultat exact** : 10 cartes routeur FRR rendues, ex. `uk-lon-core-01 · 10.200.0.13 ·
BGP ✅ 6/6 · OSPF ✅ 9/9 · Optics ✅ · IFACE 52%` — les compteurs BGP/OSPF correspondent
exactement à ce qu'on obtient en direct via `vtysh` (vérifié dans SETUP_GUIDE.md §5).

🟡 **Anomalie mineure** : `CPU` et `MEM` affichent toujours `—%` (tiret, pas de valeur)
pour les 10 routeurs FRR — seul `IFACE` (utilisation interface) est peuplé. Le
collecteur ne semble pas remonter CPU/mémoire pour ce type d'appareil dans ce lab.
Sans impact fonctionnel (le reste de la carte est correct), juste une case vide.

---

## OBSERVE

### Live Telemetry → gNMI Telemetry (`mv-gnmi`)
**Catégorie : LIVE.** Simule un abonnement gNMI (OpenConfig) en traduisant le chemin
OC choisi (ex. `openconfig-bgp:bgp/neighbors`) en commande `vtysh` réelle exécutée par
SSH sur le routeur sélectionné.

**Action testée** : device `de-fra-core-01`, path `openconfig-bgp:bgp/neighbors`, clic
"▶ Subscribe".
**Résultat exact** : `✓ Live from de-fra-core-01 via SSH→vtysh` puis le JSON brut avec
`"command":"show bgp summary"`, `"elapsed":0.52`, `"ok":1`, et le vrai texte de sortie
vtysh. 🟢 Fonctionne, et honnête sur le mécanisme (traduction OC→CLI, pas un vrai stack
gNMI/OpenConfig côté routeur — FRR ne parle pas gNMI nativement dans cette image).

### Live Telemetry → Streaming Telemetry (`telemetry`)
**Catégorie : LIVE** (Telegraf → InfluxDB → Grafana, cf. `network-lab/telemetry/`).
Panneau d'info + bouton "▶ Start Stream" par device. Non testé plus en profondeur ici
(l'architecture Telegraf/InfluxDB/Grafana est vérifiée fonctionnelle côté conteneurs
dans SETUP_GUIDE.md — `frr-telemetry` et `influxdb` tournent tous les deux).

### Syslog (`mv-syslog`)
**Catégorie : DEMO** (malgré le libellé "live receiver"). Il y a un vrai récepteur UDP
:5140 qui écoute (`start_syslog_receiver()` dans `multivendor_extensions.py`), donc le
badge "25 events from live receiver (UDP :5140)" est *techniquement* vrai — mais rien
dans ce lab n'envoie de syslog à ce port. Ce que tu vois vient de
`inject_demo_syslog()`, qui pioche au hasard dans une liste de 14 messages
préécrits couvrant tous les vendeurs (Juniper, Cisco-style, Arista, FRR) — d'où des
lignes comme `%LINEPROTO-5-UPDOWN: ... GigabitEthernet0/0/0 ...` qui ne peuvent
physiquement pas venir d'un routeur FRR (ses interfaces s'appellent eth0/eth1, pas
GigabitEthernet). 🟡 Le badge "live receiver" est donc trompeur tel quel — le
mécanisme de réception est réel, le contenu affiché ne l'est pas.

### SNMP Traps (`mv-snmp`)
**Catégorie : DEMO**, même mécanisme que Syslog (vrai récepteur UDP :1162, contenu
injecté). Résultat observé : "12 traps from live receiver (UDP :1162)", toutes avec
`— no bindings`. Même remarque 🟡 que ci-dessus.

### Alert Management → Alert Correlation (`alerts`)
**Catégorie : DEMO.** "Keep (keephq)" — agrège des alertes LibreNMS/Kibana/Grafana/Jira
fictives (aucun de ces outils n'existe dans ce lab) et les fait passer par un moteur de
corrélation pour illustrer "50 alertes → 1 incident racine".
**Action testée** : "🔄 Load Live Alerts" → 8 alertes brutes affichées (CPU spike,
BGP flap, storage latency, ticket Jira de maintenance...). Cohérent avec sa vocation de
démo pédagogique. 🟢 fonctionne comme prévu pour une démo — juste ne pas la confondre
avec une vraie alerte de ce lab.

### Alert Management → Noise Floor (`noise-floor`)
**Catégorie : DEMO**, même famille que Alert Correlation. "🔄 Refresh Trend Data" →
tableau par site (DE-FRA, EU-CDG, NL-AMS, UK-LON, US-NYC, CLAB-DC1) avec un ratio
"efficacité" (ex. 66.7%) de réduction de bruit simulée. 🟢 fonctionne comme démo.

---

## INVENTORY & AUDIT

### Inventory (`mv-inventory`)
**Catégorie : LIVE** (structure) — 41 appareils listés (26 sanitisés + 15 clab, dont 10
FRR "live"), triable/filtrable par hostname/site/vendor/modèle. Déjà vérifié dans
SETUP_GUIDE.md. 🟢

### NetBox SoT (`netbox-sot`)
**Catégorie : DEMO**, explicitement badgé "mode: simulated" dans l'UI (honnête). Compare
un inventaire NetBox fictif (26 devices) à ce qui est "observé" dans le lab (41, incluant
les 15 nœuds de la fabric clab CLOS-EVPN qu'on n'a **pas** déployée dans cette passe —
voir SETUP_GUIDE.md, Lab B hors périmètre). Résultat : 20 lignes de drift `CRITICAL`,
presque toutes `presence: missing (NetBox) → present (observé)` pour spine1-3/leaf1-6/
host1-6 — cohérent avec le fait que ces nœuds clab ne sont dans aucun inventaire réel
ici. 🟢 fonctionne comme démo, correctement étiqueté.

### Fleet Audit (`mv-fleet`) 🔴 BUG BACKEND TROUVÉ
**Catégorie : censée être LIVE** ("Fleet Config Audit — All 16 Static Devices" — audit
par règles des 16 configs Juniper/Arista sanitisées, pas les routeurs FRR).
**Action testée** : clic "▶ Run Audit".
**Résultat affiché** : `8 CRITICAL · 24 WARNINGS · 248 PASSED · 79 AVG SCORE`, avec le
message **"Demo data — start Flask for live analysis"** — trompeur, Flask tournait
bel et bien (c'est lui qui sert toute cette page).

**Root cause trouvée** (`src/multivendor_extensions.py`, route
`POST /api/mv/batfish/fleet`) : j'ai testé la route directement —

```bash
curl -X POST http://127.0.0.1:5757/api/mv/batfish/fleet -H "X-API-Key: ..." -d '{}'
# → HTTP 200, mais les 16 devices renvoient tous
#   "error": "config not found: ...\\network-lab\\demo-devices\\eos/nl-ams-eos-rt-01.txt"
```

Le code construit le nom de fichier attendu à partir du **hostname complet** de
l'inventaire (`nl-ams-eos-rt-01`, `de-fra-fw-01`, préfixé par la région — cohérent avec
tout le reste de l'UI), mais les fichiers réels sur disque
(`network-lab/demo-devices/eos/`, `.../junos/`) sont nommés **sans le préfixe région** :

```
$ ls network-lab/demo-devices/eos/
ams-eos-rt-01.txt   ams-eos-sw-01.txt   cdg-eos-rt-01.txt   fra-eos-rt-01.txt ...
```

→ `nl-ams-eos-rt-01` (attendu par le code) ≠ `ams-eos-rt-01.txt` (fichier réel) pour
les 16 devices, sans exception. Résultat : la route répond 200 mais avec 0 finding et
score 0 partout ; le frontend interprète ça comme "pas de `devices` exploitable" et
bascule silencieusement sur son jeu de données `FLEET_DEMO` codé en dur dans
`demo/index.html` (chiffres 8/24/248/79 fixes, toujours identiques quel que soit l'état
réel du lab) — avec un message d'erreur qui blâme Flask à tort.

**Ce n'est pas un bug Windows** — c'est un problème de données pur (nom de fichier vs
nom d'hôte), qui échouerait pareil sur macOS/Linux. Non corrigé (hors périmètre Partie 1
— bug backend/data, pas UI/accessibilité). Pour le corriger : soit renommer les 16
fichiers avec le préfixe région, soit faire correspondre le code au nom court (`ip.get
("config")` doit déjà contenir le bon chemin quelque part dans `_ALL_DEVICES` — à
vérifier lequel des deux est la source de vérité voulue).

### Compliance (`compliance`)
**Catégorie : LIVE** — vérifie BGP MD5 auth, prefix-limits, timers OSPF, router-id
explicite, area 0 sur les 10 routeurs FRR réels (pas les 16 configs statiques, à ne pas
confondre avec Fleet Audit ci-dessus).
**Action testée** : "▶ Scan All Devices".
**Résultat** : `Overall: 83% · Passed: 50 / Failed: 10` — chaque routeur FRR à 83%
(1 FAIL / 5 PASS), le FAIL étant systématiquement `[BGP-PFXLIMIT-02] BGP Prefix Limits`
avec un correctif suggéré (`neighbor <ip> maximum-prefix 1000 warning-only`). Cohérent :
aucun `frr.conf` du lab ne configure de prefix-limit BGP. 🟢 fonctionne, résultat exact
et actionnable.

### GAIT Audit (`mv-gait`)
**Catégorie : LIVE.** Déjà vérifié en détail (SETUP_GUIDE.md + session précédente) :
trail immuable JSONL de chaque action IA, coût en tokens exact. 🟢

### Postmortem (`postmortem`)
**Catégorie : LIVE.** Combine GAIT + Health Gate + événements de remédiation en rapport
markdown structuré.
**Action testée** : "🔍 Auto-detect incidents" (fenêtre par défaut).
**Résultat** : `✅ No incidents auto-detected in the last 120 minute(s)` avec une
suggestion honnête ("essaie Chaos Monkey pour en déclencher un"). 🟢 comportement
correct — je n'ai rien cassé récemment donc il n'y a effectivement rien à rapporter.

### CLI Reference (`cli-rag`)
**Catégorie : LIVE** — recherche BM25 dans un corpus de 44 345 entrées CLI
multivendor (Cisco/Juniper/Arista/FRR/Aruba/... — 17 OS différents).
**Action testée** : recherche `"show bgp summary"`.
**Résultat** : 6 résultats pertinents et classés par score (18.65, 18.57...), across
Cisco IOS et Juniper. 🟢 fonctionne bien, retrieval cohérent.

### Shadow Auditor (`shadow`, badge LAB)
**Catégorie : LIVE.** Diff en continu NetBox SoT (attendu) vs config live (réel) sur
les vrais routeurs FRR.
**Action testée** : "🔍 Run Shadow Audit" (All Checks).
**Résultat** (après ~7s — plus lent que les autres panneaux, aucune erreur) :
`19 Scanned · 0 Clean · 19 Drift Hosts · 19 Findings` — chaque routeur FRR remonte
`P1 · Missing ECMP maximum-paths config`. Cohérent : aucun `frr.conf` du lab ne
configure `maximum-paths`. 🟢 fonctionne, juste plus lent (~7s vs <1-3s pour les
autres scans) — pas grave mais tu peux avoir l'impression que ça a planté si tu
n'attends pas.

---

## DIAGNOSE

### AI Assistant → Agent Chat (`chat`) et Orchestrator (`mv-orchestrator`)
**Catégorie : LIVE**, déjà vérifiés en détail (bug d'accessibilité corrigé en Partie 1 +
tests fonctionnels dans SETUP_GUIDE.md/session précédente) : vrais appels Claude, vrai
usage de tokens, routage vers le bon agent (routing/remediation/verification/...). 🟢

### AI Assistant → AI Command (`ai-cmd`) 🟡 investigation de l'anomalie "Qwen3"

C'est le panneau que tu avais repéré avec un résultat "show system" bizarre. Voici ce
que j'ai trouvé en creusant, avec plusieurs requêtes différentes pour vérifier si
c'est reproductible.

**Comment ça marche** : tu tapes une question en langage naturel, `POST /api/ai-command`
(`src/app.py`) demande à un LLM de la traduire en commande CLI (`_NL_SYSTEM` prompt —
**qui précise bien le vendor/type de device**, donc ce n'est PAS un problème de contexte
vendor absent, contrairement à une hypothèse plausible), exécute la commande par SSH sur
le routeur FRR réel, puis redemande au LLM d'expliquer la sortie.

**Ce qui est réellement cassé — 2 choses, ni l'une ni l'autre n'est "un prompt trop
générique" :**

1. **🔴 Étiquette trompeuse "Qwen3"** — le frontend (`demo/index.html`, fonction
   `renderAIResult`) affiche **toujours** `AI Translation (Qwen3)` / `Qwen3 Explanation`,
   texte codé en dur, peu importe quel moteur a réellement répondu. Sur cette machine
   il n'y a pas d'Ollama/Docker Model Runner installé (confirmé : `GET /api/llm/status`
   → `available:false`) — donc ce n'est **jamais** Qwen3 qui répond ici. Le vrai chemin
   (`src/app.py`, `_llm_query()`) essaie dans l'ordre Ollama → Docker Model Runner →
   **Claude** (repli), et sur cette machine c'est systématiquement Claude qui répond
   (vérifié : `usage` avec de vrais tokens dans chaque réponse). Le panneau ment donc
   sur l'origine de la réponse.

2. **🔴 Lenteur sévère et non signalée** — une requête AI Command a mis **30 à 60+
   secondes** avant d'afficher un résultat chez moi, alors que rien dans l'UI n'indique
   qu'une attente aussi longue est normale (juste "🤖 Translating..." sans barre de
   progression ni estimation). Cause : `_llm_query()` (src/app.py) tente Ollama
   (`http://localhost:11434`, timeout 60s) PUIS Docker Model Runner (2 chemins, encore
   60s chacun) AVANT de retomber sur Claude — sur une machine Windows sans ces services
   installés, chaque tentative doit épuiser (ou presque) son propre timeout avant de
   passer à la suivante. Résultat : une question qui devrait prendre 2-5s (un seul
   appel Claude) en prend parfois 10 à 20× plus.

**Sur la question "prompt trop générique ?"** : non — testé avec 3 requêtes
différentes (`"show system"`, `"show interfaces"`, une question BGP) : le prompt
système envoyé au LLM contient explicitement `Device type: frr` à chaque fois. La
traduction résultante est parfois correcte (`"show system"` → `show version`, qui
fonctionne) et parfois imprécise (`"show interfaces"` → `show interfaces` laissé tel
quel, alors que la syntaxe FRR correcte est `show interface`/`show interface brief` —
la commande échoue avec `% Unknown command`). C'est une limite normale d'un
traducteur LLM (pas déterministe, pas garanti syntaxiquement correct à 100%) plutôt
qu'un bug de code — mais ça explique pourquoi tu peux voir un résultat "qui ne marche
pas" sans que rien ne soit cassé côté infrastructure : la commande traduite était juste
mauvaise, et le LLM l'explique correctement après coup ("cette commande a échoué
parce que...").

**Non corrigé** (hors périmètre Partie 1 — bug backend, pas UI/accessibilité). Deux
correctifs simples si vous voulez les faire plus tard : (a) libeller dynamiquement
selon le provider réel qui a répondu au lieu de coder "Qwen3" en dur, (b) réordonner
`_llm_query()` pour essayer Claude en premier quand `ANTHROPIC_API_KEY` est présent et
qu'aucun des deux services locaux n'a répondu au ping précédent (ou réduire drastiquement
`LLM_TIMEOUT` pour les tentatives locales).

### AI Insights → Analysis (`analysis`) 🟡 démo non étiquetée comme telle
**Catégorie : DEMO, mais présentée comme si elle analysait le device sélectionné.**
**Action testée** : sous-onglet "🧠 Deep Analysis" (device de-fra-core-01 sélectionné).
**Résultat** : `DEEP ANALYSIS DE-FRA-CORE-01 · 74/100 · Grade B` avec le détail
`Interface xe-0/0/1 link down`. **`xe-0/0/1` est une convention de nommage Juniper —
un conteneur FRR n'a jamais d'interface `xe-0/0/1`, les siennes s'appellent `eth0`/
`eth1`.** Vérifié dans le code (`demo/index.html`) : c'est un jeu de données
entièrement codé en dur (variable `iface`/`alarms`/tableaux JS, ligne ~3263+), utilisé
tel quel quel que soit le device sélectionné dans l'interface — le titre
"DE-FRA-CORE-01" change, mais le contenu ne reflète jamais le device réellement
sélectionné. Porte aussi le même label trompeur "QWEN3 LLM NARRATIVE" que AI Command
(même famille de bug qu'au-dessus). 🟡 Non trompeur si on sait que c'est une démo
(le README le dit), mais rien dans le panneau lui-même ne l'indique clairement.

### AI Insights → Doc Search (`docs`)
**Catégorie : LIVE** (LLM RAG sur la doc FRR/Junos/EOS). Non testé plus en détail
(architecture similaire à CLI Reference, déjà vérifiée fonctionnelle).

### SuzieQ (`mv-suzieq`) 🟡 filtre "FRR" toujours vide
**Catégorie : LIVE, mais scope limité — "Offline config parsing" des 16 configs
statiques Juniper/Arista uniquement**, PAS des 10 routeurs FRR live malgré "FRR"
listé comme option de filtre vendeur dans l'UI.
**Action testée** : "▶ Run" (summarize, All Vendors) puis filtre vendor → "FRR".
**Résultat** : All Vendors → `16 devices parsed`, `arista:6, juniper:10`, section
"COMMON ISSUES: Config file not found" (probablement le même bug de correspondance
hostname↔fichier que Fleet Audit, cf. plus haut — mêmes 16 fichiers concernés).
Filtre "FRR" → `0 devices parsed` systématiquement : ce panneau ne peut physiquement
jamais retourner de résultat pour FRR puisqu'il n'y a pas de fichier `.txt` FRR dans
`network-lab/demo-devices/` (les routeurs FRR ont leur config dans
`network-lab/configs/`, un dossier différent que SuzieQ ne regarde pas). 🟡 pas un
crash, mais une option de filtre qui ne peut jamais rien retourner — trompeur.

---

## OPERATE

### Terminal / Collect → CLI (`commands`)
**Catégorie : LIVE.** Boutons rapides (BGP/Interfaces/Routes/Alarms/Version/ARP/Logs)
sur le device sélectionné dans la sidebar.
**Action testée** : device `de-fra-core-01`, bouton "BGP".
**Résultat** : sortie `vtysh` réelle et complète (6 peers, Up/Down ~6h, cohérent avec le
reste de la session). 🟢

### Terminal / Collect → Collect (`collect`)
**Catégorie : LIVE.** "⚡ Quick Snapshot" (Version + BGP) / "🚨 Full Investigation"
(collecte élargie) sur le device sélectionné.
**Action testée** : Quick Snapshot sur `de-fra-core-01`. **Résultat** : Version FRR
8.4.1 réelle + BGP summary réel. 🟢

### Terminal / Collect → CLI Transport (`cli-bench`) 🔴 BUG INFRA TROUVÉ
**Catégorie : censée être LIVE** — "Collect All 10 via HTTP" est censé interroger les
10 routeurs en parallèle via le proxy HTTP `network-lab/cli_proxy.py` (port 8080 de
chaque conteneur), pas par SSH.
**Action testée** : "⚡ Collect All 10 via HTTP".
**Résultat affiché** : `0/10 devices · 97ms total` — chaque routeur en `(no output)`.

**Root cause trouvée** : `docker exec de-fra-core-01 cat /var/log/cli_proxy.log` →
```
FATAL: CLI_PROXY_PASSWORD is not set — refusing to start.
```
`network-lab/entrypoint.sh` lance `python3 /usr/local/bin/cli_proxy.py` à l'intérieur
du conteneur, qui exige la variable d'environnement `CLI_PROXY_PASSWORD` (fail-closed —
c'est voulu, pour ne pas exposer un shell CLI sans mot de passe). Mais
`network-lab/docker-compose.yml` **ne passe cette variable à aucun des 10 services
routeur** (pas de bloc `environment:` dessus — seuls `influxdb`/`grafana`/
`frr-telemetry` en ont un). Le mot de passe qu'on a généré dans `src/.env` (utilisé
par le Flask `app.py` côté hôte pour s'authentifier AU proxy) n'est donc jamais
transmis au proxy lui-même : il ne démarre jamais, `python3 ... &` échoue
silencieusement, aucun listener sur le port 8080 dans le conteneur → connexions
refusées côté hôte → "0/10, (no output)" pour tout le monde.

**Pas un bug Windows** — pur oubli dans `docker-compose.yml`, identique sur toute
plateforme. Non corrigé (hors périmètre Partie 1). Pour corriger : ajouter
```yaml
environment:
  - CLI_PROXY_PASSWORD=${CLI_PROXY_PASSWORD}
```
à chacun des 10 services routeur dans `network-lab/docker-compose.yml`, et créer un
`network-lab/.env` (docker compose ne lit **que** le `.env` du même dossier que le
`docker-compose.yml` qu'on lance — pas `src/.env`) contenant `CLI_PROXY_PASSWORD=...`.

### NAPALM (`napalm`) 🟡 données figées, pas liées au device réel
**Catégorie : DEMO**, malgré la sélection de site/tâche qui donne l'impression d'une
requête live.
**Action testée** : site DE-FRA, tâche "BGP Status".
**Résultat affiché** : `de-fra-edge-01 Peers: 2/3 — 1 DOWN (10.200.0.15 AS65005),
Flapping: 8 flaps/24h`.
**Vérifié faux** : `docker exec de-fra-edge-01 vtysh -c 'show bgp summary'` montre
**3/3 pairs Established**, aucun voisin `10.200.0.15` configuré du tout sur ce routeur
(ce n'est même pas un pair théorique). Confirmé dans le code (`demo/index.html`) :
chaîne de caractères codée en dur, jamais recalculée. 🟡 même famille que "Deep
Analysis" plus haut — présenté comme une requête live sur le site choisi, en réalité
figé.

### Nornir Engine (`nornir`) 🔴 faux-positif "WARN" systématique — bug de mots-clés
**Catégorie : LIVE** (et je peux le prouver — sortie `vtysh` réelle, timestamps réels,
0.93s d'exécution parallèle réelle). C'est justement ce qui rend le bug intéressant :
la donnée est bonne, le **diagnostic** posé dessus est faux.
**Action testée** : site DE-FRA, tâche "BGP Health Check", "⚡ Run Parallel Task".
**Résultat** : `4 WARNING / 0 OK / 0 ERROR` sur 4 routeurs — alors que la sortie brute
montre les 4 routeurs avec **tous leurs pairs BGP Established**, aucun problème réel
(vérifié champ par champ dans la réponse JSON).

**Root cause trouvée** (`src/app.py`, fonction `_nornir_worker`, ~ligne 12139) :
```python
lower = out_text.lower()
if "error" in lower or "alarm" in lower or "down" in lower:
    status = "warn"
```
Classification par simple recherche de sous-chaîne sur tout le texte de sortie, sans
parser structurellement les champs. Or l'en-tête de colonne standard de `show bgp
summary` s'appelle littéralement **`Up/Down`** — qui contient la sous-chaîne `"down"`.
**Toute** sortie BGP saine déclenche donc ce test, marquant systématiquement les
appareils en bonne santé comme `warn`. Le même piège existe pour `"error"` (qui peut
apparaître dans des noms de champs légitimes) et `"alarm"` selon la commande.

**Pas un bug Windows.** Non corrigé (hors périmètre Partie 1 — logique backend). Pour
corriger : parser les champs structurés (State/PfxRcd par pair) plutôt que chercher des
mots dans le texte brut, ou au minimum exclure les en-têtes de colonnes du test.

---

## 🔴 CONSTAT TRANSVERSAL : lenteur / échec des fonctionnalités LLM sur cette machine

Ce n'est pas propre à un panneau — ça touche **tout ce qui appelle `_llm_query()`**
côté backend (`src/app.py`) : AI Command, Doc Search, la narration "Qwen3" de Deep
Analysis, la recommandation "Qwen3" de Batfish.

**Cause** : `_llm_query()` essaie dans l'ordre **Ollama** (`localhost:11434`) puis
**Docker Model Runner** (`localhost:12434`, 2 chemins) — chacun avec un timeout de
**60s** (`LLM_TIMEOUT`) — et ne retombe sur **Claude** (le seul provider réellement
configuré sur cette machine, via `ANTHROPIC_API_KEY`) qu'en dernier recours. Résultat
mesuré :
- **AI Command** : 30 à 60+ secondes par requête (a fini par répondre correctement).
- **Doc Search** : `net::ERR_ABORTED` — le timeout **côté navigateur** (plus court que
  la chaîne de fallback côté serveur) coupe la requête avant que Claude n'ait sa
  chance de répondre. Testé avec `"OSPF stuck in ExStart"` → échec systématique.

**Pas un bug Windows en soi** — le mécanisme de fallback est le même sur toute
plateforme, mais je n'ai pas pu vérifier si l'absence de réponse aux ports 11434/12434
échoue aussi vite sur macOS/Linux (connexion refusée quasi instantanée) qu'elle traîne
ici (pouvant approcher le timeout complet) — à vérifier si vous testez sur une autre
machine. **Non corrigé** (backend, hors périmètre Partie 1). Deux pistes : (a) sonder
Ollama/Docker Model Runner avec un timeout très court (1-2s) avant de s'engager dans
l'attente complète, ou (b) basculer l'ordre par défaut sur `LLM_PROVIDER=claude` quand
`ANTHROPIC_API_KEY` est présent et qu'aucun des deux services locaux n'est déjà
confirmé disponible (cf. `GET /api/llm/status`, qui sait déjà dire `available:false`
en amont — juste pas utilisé pour réordonner `_llm_query()`).

---

## CHANGE CONTROL

### Change Approval (`change-approval`)
**Catégorie : LIVE** (génération de commandes) — souffre du même ralentissement LLM
que ci-dessus mais **finit par aboutir**.
**Action testée** : device `de-fra-core-01`, intent `"Add BGP log-neighbor-changes to
all peers"`, "💡 AI Propose".
**Résultat** (après ~15-20s) : proposition syntaxiquement correcte —
```
configure terminal
router bgp
 bgp log-neighbor-changes
end
write memory
```
— plus un PRE-snapshot (`show bgp summary`, `show ip ospf neighbor`,
`show running-config`) capturé automatiquement. Boutons "✅ Approve & Apply" / "✗ Deny"
présents ; **je n'ai pas cliqué Approve** pour ne pas modifier un routeur du lab pour
les besoins du test — à toi de tester ce chemin si tu veux voir le diff POST. 🟢

### Health Gate (`health-gate`)
**Catégorie : DEMO, badgé "mode: simulated"** (honnête). Workflow "commit-confirmed" à
4 phases inspiré de RFC 6241 §8.4.
**Action testée** : device `de-fra-core-01`, scénario "Clean window (confirms)",
"▶ Apply with Health Gate".
**Résultat** (après ~30s, fenêtre de watch) : `PHASE 4 · VERDICT: ✅ confirmed`, 6
échantillons de surveillance (poll 5s), 0 régression. 🟢 fonctionne exactement comme
conçu — logique saine, verdict cohérent avec le scénario "clean" choisi.

### Auto-Remediate (`auto-remediate`) 🟢 bon exemple de garde-fou
**Catégorie : LIVE** (tire le vrai drift de NetBox SoT vu plus haut).
**Action testée** : "↩ Propose for all current drift".
**Résultat** : `imported 20/20 drift rows` → 2 en PENDING (auto-fixables), **18
rejetés automatiquement** avec la raison `"Extra-in-lab device — needs human triage,
not auto-remediation."` — c'est le mécanisme qui empêche l'auto-remédiation de
"corriger" les nœuds clab absents de NetBox (qui ne sont pas de vrais problèmes, juste
un inventaire de démo incomplet). 🟢 bon exemple de conception prudente à mentionner
dans le mémoire : l'IA sait dire "je ne touche pas à ça, un humain doit trancher".

### Observer-Actor (`observer`)
**Catégorie : LIVE**, réutilise les snapshots PRE/POST de l'onglet State Diff.
**Action testée** : "⚡ Run Feedback Loop" sur `de-fra-core-01` (avec les snapshots
PRE/POST déjà pris ci-dessous, identiques).
**Résultat** : `✅ No unintended changes detected — state is healthy.` 🟢 cohérent.

### State Diff (`statediff`)
**Catégorie : LIVE.** PRE Snapshot → POST Snapshot → Compare.
**Résultat** : `0 change(s) detected` entre deux snapshots pris à 21s d'intervalle sans
rien modifier entre les deux — comportement correct. Note honnête affichée : `Note: No
module named 'napalm' — FRR devices: SSH-based collection used instead` (cohérent avec
SETUP_GUIDE.md, NAPALM n'est pas dans `requirements.txt`). 🟢

### Change Pipeline (carte "hero", 5 étapes : Pre-Deploy → Health Gate → Approval →
Auto-Remediate → Postmortem)
Simple raccourci visuel vers les 5 panneaux ci-dessus/ci-dessous — chaque étape a été
testée individuellement plus haut. 🟢

---

## VERIFY & TEST

### Pre-Deploy Analysis → Pre-Deploy (`batfish`)
**Catégorie : LIVE** (règles) — souffre du même problème d'étiquette "Qwen3" en fin de
sortie ("🟢 Qwen3 Live Recommendation") que AI Command/Deep Analysis (même bug, pas
reدécrit en détail ici).
**Action testée** : "📋 Load Sample Config" (Juniper) → "🔍 Validate Config".
**Résultat** : `2 Errors · 2 Warnings · 3 Passed` — findings précis et corrects (clé
BGP en clair, export-policy manquante, dead-interval OSPF élevé, hold-time BGP élevé).
🟢 le moteur de règles fonctionne bien, seule l'étiquette de la recommandation finale
ment sur son origine.

### Pre-Deploy Analysis → Blast Radius (`blast`)
**Catégorie : LIVE/heuristique** (BFS sur la topologie + règles Batfish).
**Action testée** : "📋 Load Example" → "💥 Analyse Blast Radius".
**Résultat** : heatmap cohérente — `1 erreur (origine) · 2 warnings · 5 directement
affectés · 3 transitivement affectés` sur les 10 routeurs FRR réels. 🟢

### Topology → BGP Topology (`topo`)
**Catégorie : LIVE.** "▶ Build Live Topology".
**Résultat** : `🟢 Live · 41 devices · 36 BGP sessions`, toutes marquées `BGP ✓`,
cohérent avec tous les autres panneaux qui comptent les sessions BGP. 🟢

### Topology → OSPF Discover (`discover`)
**Catégorie : LIVE** ("Live Neighbor Walk" par SSH, sans CSV statique).
**Action testée** : "▶ Discover Topology".
**Résultat** : `10 nodes · 45 links` — les 10 routeurs FRR avec leurs vraies IP de
management et les adjacences OSPF réelles (ex. `uk-lon-core-01 ↔ de-fra-core-01
[OSPF 2-Way/DROther]`). 🟢 excellent, correspond exactement à `show ip ospf neighbor`.

### Path Trace (`mv-path`)
**Catégorie : LIVE** (BFS sur le graphe BGP réel).
**Action testée** : `de-fra-core-01 → us-nyc-core-01` (vérifié via la requête réseau
brute, mon script de clic sur les `<select>` n'a pas affiché le résultat correctement
— **limite de mon script de test, pas un bug du produit**).
**Résultat backend** : chemin direct 1 hop via eBGP (`de-fra-core-01 → us-nyc-core-01`,
type `eBGP`), cohérent avec le maillage eBGP full-mesh entre cœurs. 🟢

### Intent Verify (`mv-intent`)
**Catégorie : LIVE.** "▶ Verify" compare la config "revendiquée" (fichiers
`network-lab/configs/`) à l'état observé (vrais routeurs).
**Résultat** : `Intent Score: 100% · 0 drift events · 52 sessions checked`. 🟢 aucune
dérive détectée — cohérent, on n'a rien modifié manuellement sur les routeurs.

### Eval Harness (`mv-eval`) et Chaos Monkey (`chaos`)
Déjà testés en détail dans une session précédente (voir aussi SETUP_GUIDE.md, section
"Known bugs") : Eval Harness 🟢 fonctionne, avec un bug connu sur le score LLM Judge
(troncature à `max_tokens=400`, score 0 parfois sur des diagnostics longs — Bug #3 du
SETUP_GUIDE). Chaos Monkey 🔴 cassé sous Windows (mauvaise gestion des antislashs
Windows par Git Bash quand `subprocess.run(["bash", ...])` est appelé — Bug #4 du
SETUP_GUIDE) : les boutons Break/Fix ne touchent jamais vraiment aux routeurs, mais
l'UI affiche un faux état "down" — voir SETUP_GUIDE.md pour les détails complets.

---

## Récapitulatif — tous les bugs trouvés dans cette passe

| # | Panneau | Sévérité | Résumé | Root cause | Statut |
|---|---|---|---|---|---|
| 1 | Agent Chat | 🔴 UI | Champ de saisie masqué par le dock ouvert | CSS `!important` figeait le dock en position ouverte en permanence | ✅ **corrigé** (Partie 1) |
| 2 | 6 panneaux (dock, alertes, topo, terminal, ribbon, triage) | 🔴 a11y | `aria-hidden` posé sur un élément encore focus | Pas de `blur()` avant `aria-hidden=true` | ✅ **corrigé** (Partie 1) |
| 3 | Fleet Audit | 🔴 backend | Toujours "Demo data", jamais de vraie analyse | Nom de fichier attendu (`nl-ams-...`) ≠ nom réel sur disque (`ams-...`) | 📝 documenté seulement |
| 4 | AI Command | 🔴 backend | Étiquette "Qwen3" alors que c'est Claude qui répond | Texte codé en dur dans le frontend, ignore le provider réel | 📝 documenté seulement |
| 5 | AI Command / Doc Search / LLM en général | 🔴 perf | 30-60s+ de latence, parfois `ERR_ABORTED` | `_llm_query()` tente Ollama puis Docker Model Runner (60s chacun) avant Claude | 📝 documenté seulement |
| 6 | CLI Transport | 🔴 infra | Proxy HTTP jamais démarré, 0/10 partout | `CLI_PROXY_PASSWORD` jamais passé aux conteneurs dans `docker-compose.yml` | 📝 documenté seulement |
| 7 | Nornir — BGP Health Check | 🔴 backend | Tous les routeurs sains classés "WARN" | Recherche de sous-chaîne `"down"` qui matche l'en-tête de colonne `Up/Down` | 📝 documenté seulement |
| 8 | NAPALM, Deep Analysis | 🟡 UX | Données figées présentées comme si live | Datasets codés en dur dans `demo/index.html`, non liés au device sélectionné | 📝 documenté seulement |
| 9 | Syslog, SNMP Traps | 🟡 UX | Badge "live receiver" trompeur | Vrai récepteur UDP, mais contenu 100% injecté par `inject_demo_syslog()` | 📝 documenté seulement |
| 10 | SuzieQ | 🟡 UX | Filtre "FRR" toujours 0 résultat | Le parseur ne couvre que les 16 configs statiques, jamais les routeurs FRR | 📝 documenté seulement |
| 11 | Eval Harness | 🟡 backend | LLM Judge renvoie parfois `score:0,error:true` | `max_tokens=400` tronque le JSON du juge sur les diagnostics longs | 📝 déjà documenté (SETUP_GUIDE Bug #3) |
| 12 | Chaos Monkey | 🔴 Windows | Break/Fix ne touchent jamais les routeurs | `subprocess` perd les antislashs Windows en appelant `bash.exe` | 📝 déjà documenté (SETUP_GUIDE Bug #4) |

**Tout le reste testé (≈30 autres panneaux) fonctionne comme attendu** — soit en LIVE
(donnée réelle, vérifiée croisée avec `vtysh`/`docker exec` à chaque fois que c'était
possible), soit en DEMO clairement dans l'esprit du projet (portfolio/démo, pas un
outil de prod — cf. `README.md` § "Engineering notes"). Le détail panneau par panneau
ci-dessus donne, pour chacun, l'action exacte à reproduire et le résultat attendu.


