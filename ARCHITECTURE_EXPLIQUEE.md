# Architecture expliquée — AI Network Tool v4.0

Document écrit pour toi : ingénieur télécoms (BGP/OSPF/routage — pas besoin de te
réexpliquer ça) mais néophyte sur la partie logicielle/IA de ce lab. Objectif : que tu
puisses répondre sans hésiter à *"comment fonctionne ce lab, concrètement, du clic à
la réponse ?"*.

Deux parties totalement indépendantes à ne jamais confondre :
- **Le lab réseau** (`network-lab/`) — 10 vrais routeurs FRR, dans des vrais
  conteneurs Docker, qui font du vrai BGP/OSPF/BFD entre eux. Rien de simulé.
- **L'outil logiciel/IA** (`src/`) — une appli Flask + un orchestrateur IA qui
  *observent* ce lab (et 16 autres appareils Juniper/Arista, mais sous forme de
  fichiers de config statiques, pas de vrais appareils) et l'exposent dans une UI web.

---

## Partie 1 — Architecture réseau

### 1.1 Topologie exacte des 10 routeurs FRR

Tous les routeurs sont des conteneurs Docker (image `frrouting/frr:v8.4.1`,
`network-lab/Dockerfile`), sur un **unique réseau management à plat**
`10.200.0.0/24` (pas de séparation par VLAN/VRF — chaque routeur voit directement
les IP de management de tous les autres, et c'est *par ce réseau* que le BGP/OSPF de
laboratoire circule — ce n'est pas un "vrai" plan d'adressage WAN inter-sites, c'est
une convention de lab pour que tout tienne dans un seul `docker-compose.yml`).

| Hostname | Rôle | Site | AS BGP | Router-ID OSPF | IP mgmt |
|---|---|---|---|---|---|
| de-fra-core-01 | core | DE-FRA | 65001 | 10.255.0.1 | 10.200.0.11 |
| de-fra-core-02 | core | DE-FRA | 65002 | 10.255.0.2 | 10.200.0.12 |
| uk-lon-core-01 | core | UK-LON | 65003 | 10.255.0.3 | 10.200.0.13 |
| nl-ams-core-01 | core | NL-AMS | 65004 | 10.255.0.4 | 10.200.0.14 |
| us-nyc-core-01 | core | US-NYC | 65005 | 10.255.0.5 | 10.200.0.15 |
| de-fra-edge-01 | edge | DE-FRA | 65006 | 10.255.0.6 | 10.200.0.21 |
| uk-lon-edge-01 | edge | UK-LON | 65007 | 10.255.0.7 | 10.200.0.22 |
| nl-ams-edge-01 | edge | NL-AMS | 65008 | 10.255.0.8 | 10.200.0.23 |
| de-fra-dist-01 | dist | DE-FRA | 65009 | 10.255.0.9 | 10.200.0.33 |
| uk-lon-dist-01 | dist | UK-LON | 65010 | 10.255.0.10 | 10.200.0.31 |

Chaque AS = 1 routeur (c'est un modèle eBGP "1 AS par device", pas un vrai IGP+eBGP
d'entreprise à AS partagé par site). **US-NYC n'a ni edge ni dist dédié** — voir plus
bas, son "edge" logique est en fait mutualisé avec UK-LON. **EU-CDG apparaît dans
l'UI** (Inventory, NetBox SoT...) **mais n'a aucun routeur FRR live** — ce site n'existe
que dans les 16 configs statiques sanitisées (`eu-cdg-mx-01`, `eu-cdg-eos-rt-01`), pas
dans le lab Docker. Ne cherche pas de conteneur `eu-cdg-*` — il n'y en a pas.

### 1.2 Qui parle à qui (topologie BGP, vérifiée dans les 10 `frr.conf`)

```
                         ┌─────────────────────────────────────────────┐
                         │        CŒUR — full-mesh partiel eBGP         │
                         │                                               │
        ┌───────────────┤  de-fra-core-01 (65001) ═══ de-fra-core-02 (65002)
        │                │        ║   ╲         ╱   ║        │
        │                │        ║    ╲       ╱    ║        │
   de-fra-edge-01         ║     ╲     ╱     ║        │
   de-fra-dist-01         ║      ╲   ╱      ║        │
   (65006/65009,          ║       ╲ ╱       ║        │
    dual-homés aux        ║        ╳        ║        │
    2 cores DE-FRA        ║       ╱ ╲       ║        │
    + entre eux)          ║      ╱   ╲      ║        │
        │                 ║     ╱     ╲     ║        │
        │                uk-lon-core-01 (65003)  nl-ams-core-01 (65004)
        │                 ║      ╲                      │
        │                 ║       ╲                     │
        │           us-nyc-core-01 (65005)      nl-ams-edge-01 (65008)
        │                 │         ╲             (single-homé,
        │           uk-lon-edge-01   ╲             pas de dist)
        │           (65007) ──────────┘
        │           dual-homé À DEUX SITES :
        │           son propre core (UK-LON) ET us-nyc-core-01 !
        │                 │
   uk-lon-dist-01 (65010)
   (single-homé à uk-lon-core-01
    seulement — pas de edge↔dist
    local comme à DE-FRA)
```

**Détails précis (tirés des `neighbor ... remote-as` de chaque `frr.conf`, pas
déduits) :**

- **Maillage cœur (5 nœuds) : PAS un full-mesh complet.** 9 liens sur les 10
  possibles — il manque **nl-ams-core-01 ↔ us-nyc-core-01** (AMS et NYC ne sont
  peerés directement nulle part). Pour joindre AMS↔NYC, le trafic doit passer par
  un cœur intermédiaire (DE-FRA ou LON).
- **DE-FRA est le site le plus redondant** : son edge et son dist sont tous les deux
  dual-homés vers les **deux** cores DE-FRA, **et** peerés directement entre eux
  (edge↔dist) — triangle complet.
- **UK-LON est asymétrique** : son edge (`uk-lon-edge-01`) est dual-homé, mais pas
  vers son propre dist — vers son propre core **et** vers `us-nyc-core-01`, un cœur
  d'un **autre site**. C'est ce lien cross-site qui fait office de connectivité
  "edge" pour US-NYC (qui n'a pas de edge à lui). Son dist (`uk-lon-dist-01`) est
  lui single-homé, seulement vers `uk-lon-core-01`.
- **NL-AMS est le moins redondant** : son edge est single-homé à son unique core
  local, pas de dist du tout.

Ce n'est pas un bug — c'est un choix délibéré pour que le lab illustre plusieurs
patterns de redondance différents (full dual-homing, cross-site backup, single-homed)
avec seulement 10 routeurs, plutôt que de répéter le même pattern 5 fois.

### 1.3 Pourquoi BGP + OSPF + BFD ensemble, précisément dans ce lab

Tu connais la théorie générale ; voici le rôle exact de chaque protocole **dans ce
lab spécifiquement** :

- **OSPF (une seule aire, `area 0.0.0.0`, sur tous les 10 routeurs)** — sert
  uniquement à faire apprendre à chaque routeur les **loopbacks** (`10.255.0.x/32`)
  de tous les autres, pour que les sessions eBGP puissent s'établir même quand elles
  ne sont pas configurées en `ebgp-multihop` (une session eBGP entre deux loopbacks
  routées via OSPF plutôt que directement sur l'IP mgmt visible). Aucune route
  "métier" ne transite par OSPF — il ne sert que de plomberie d'accessibilité pour
  BGP. C'est pour ça que `passive-interface lo` est configuré partout : la loopback
  participe à OSPF (annoncée) mais n'essaie pas d'y faire des adjacences dessus.
- **BGP (eBGP entre AS différents, un AS par routeur)** — c'est **le seul protocole
  qui porte les vraies routes** (`network 10.10.x.0/24` par site + les loopbacks).
  Chaque session a un mot de passe MD5 (`neighbor ... password Gesh!Bgp...`),
  `graceful-restart` activé, et des timers keepalive/hold à `10 30` (au lieu du
  défaut `60 180`) — resserrés exprès pour que le lab converge/détecte vite,
  cohérent avec un environnement de démo où on veut voir les effets d'une panne
  rapidement.
- **BFD (BFD, un peer par voisin BGP)** — détection de panne **sub-seconde**,
  indépendante des timers BGP eux-mêmes. Sans BFD, une coupure de lien ne serait
  détectée qu'au bout du hold-timer BGP (30s ici). Avec BFD, c'est quasi immédiat.
  Timers actuels (après le fix documenté dans `SETUP_GUIDE.md`/l'historique git) :
  `receive-interval 300 / transmit-interval 300 / detect-multiplier 3` → détection
  en ~900ms. Ces timers ont été **volontairement relâchés** (ils étaient à 100ms×3 =
  300ms à l'origine) parce que sur un hôte à seulement 2 vCPU, un simple pic de
  charge CPU pouvait dépasser 300ms et déclencher un faux-positif BFD → flap BGP en
  cascade. 900ms donne de la marge tout en restant largement sub-seconde par rapport
  au hold-timer BGP de 30s.

**En résumé du rôle de chacun dans ce lab** : OSPF = "comment j'atteins la loopback de
mon voisin BGP", BGP = "quelles routes j'annonce/reçois réellement", BFD = "je
détecte une panne de lien 33× plus vite que BGP seul ne le ferait".

### 1.4 Comment les 16 configs statiques Juniper/Arista s'intègrent à l'Inventory

Le panneau **Inventory** (voir `UI_TESTING_LOG.md`) affiche **41 appareils** au total,
mélange de trois origines complètement différentes :

1. **10 routeurs FRR live** (ce lab) — vraies commandes SSH à chaque clic.
2. **16 configs statiques sanitisées** (`network-lab/demo-devices/{junos,eos}/*.txt`)
   — de vrais extraits de configuration Juniper/Arista, anonymisés (adresses/clés
   remplacées), mais **figés** : ce sont des fichiers texte, il n'y a **aucun
   appareil réel derrière**. Les panneaux qui les utilisent (Fleet Audit, Compliance
   côté statique, SuzieQ, Batfish/Pre-Deploy) font du **parsing de texte**, pas de
   requête réseau.
3. **15 nœuds de la fabric containerlab CLOS-EVPN** (`clab-dc1` — spine1-3, leaf1-6,
   host1-6) — **jamais déployés** dans cette passe (Lab B, hors périmètre, voir
   `SETUP_GUIDE.md`). Ils apparaissent dans l'inventaire/les compteurs mais aucune
   commande ne peut réellement leur être envoyée tant que Lab B n'est pas lancé.

Le fichier `network-lab/demo-devices/inventory.json` (+ le code Python qui fusionne
avec la liste des 10 routeurs FRR au démarrage de `app.py`) est ce qui fait tenir ces
41 entrées dans **une seule et même table** malgré leurs natures radicalement
différentes. C'est une force pédagogique du projet (montrer un tableau de bord
"multivendor unifié") mais aussi la source de plusieurs bugs documentés dans
`UI_TESTING_LOG.md` (ex. Fleet Audit : le nom de fichier attendu pour les configs
statiques ne correspond pas au nom réel sur disque).

---

## Partie 2 — Architecture logicielle / IA

### 2.1 Vue d'ensemble : qui appelle qui

```
┌──────────────────────────────────────────────────────────────────────────┐
│  NAVIGATEUR — demo/index.html (~10 000 lignes HTML+CSS+JS, une seule page)│
│  Aucun framework (pas de React/Vue) — JS "vanilla", fetch() vers l'API    │
└──────────────────────────────┬───────────────────────────────────────────┘
                                │ HTTP (fetch, header X-API-Key)
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    src/app.py — Flask, port :5757                        │
│  Le "monolithe" : ~15 000 lignes, toutes les routes /api/*               │
│  + src/multivendor_extensions.py (Blueprint mv_bp, /api/mv/*, plus récent│
│    et mieux structuré que app.py — cf. README "Engineering notes")       │
└───┬─────────────┬──────────────┬──────────────┬──────────────┬──────────┘
    │              │              │              │              │
    ▼              ▼              ▼              ▼              ▼
┌────────┐  ┌─────────────┐  ┌─────────┐  ┌────────────┐  ┌───────────┐
│drivers/│  │pydantic_ai_ │  │eval_    │  │gait_audit  │  │ network-  │
│ (SSH / │  │orchestrator │  │harness  │  │ .py        │  │ lab/ 10   │
│ docker │  │   .py       │  │  .py    │  │(trail JSONL│  │ conteneurs│
│ exec)  │  │ (agent IA)  │  │         │  │ append-only│  │ FRR       │
└───┬────┘  └──────┬──────┘  └────┬────┘  └────────────┘  └─────┬─────┘
    │              │ appelle Claude│                             │
    │              ▼ (Anthropic SDK)                              │
    │        ┌──────────────┐                                     │
    │        │ Claude API   │                                     │
    │        │(claude-haiku-│                                     │
    │        │    4-5)      │                                     │
    │        └──────────────┘                                     │
    └─────────────── SSH (paramiko/netmiko) ou docker exec ────────┘
                          vers les 10 routeurs FRR réels
```

**Deux façons distinctes d'atteindre un routeur** existent dans le code (héritage de
l'évolution du projet, pas un choix ambigu) :
- **`docker exec <container> vtysh -c '<commande>'`** — le chemin le plus utilisé
  pour les routeurs FRR du lab (rapide, pas besoin de clé SSH). Utilisé par
  ex. par la couche `drivers/` (`DockerExecTransport`) et une bonne partie de
  `app.py`.
- **SSH réel** (paramiko/netmiko, via la clé `network-lab/ssh-keys/lab_key`) — utilisé
  quand le code veut simuler ce qu'il ferait sur un **vrai** appareil en prod
  (où `docker exec` n'existe évidemment pas), ex. `_nornir_worker`, le Health Gate,
  le CLI proxy HTTP (`cli_proxy.py`, port 8080 par conteneur, cassé sur cette machine
  — voir `UI_TESTING_LOG.md`).

### 2.2 Glossaire minimal AVANT la suite

Voir le glossaire complet en fin de document — mais deux mots reviennent tout de suite :
- **Agent** : ici, une fonction Python qui reçoit une question, appelle Claude avec un
  prompt système spécialisé (ex. "tu es un expert BGP/OSPF"), et retourne une réponse
  **structurée** (objet Python typé, pas juste du texte libre).
- **Orchestrateur** : le "aiguilleur" qui décide QUEL agent appeler selon la question
  posée (routing ? ACL ? incident ?), avant de lui déléguer le travail.

### 2.3 L'orchestrateur (`src/pydantic_ai_orchestrator.py`) — comment ça route

```python
classify(prompt) -> "routing" | "acl" | "incident"     # heuristique par mots-clés,
                                                          # PAS un appel LLM — juste
                                                          # des if/else sur des mots
                                                          # ("bgp","ospf" → routing ;
                                                          #  "acl","firewall" → acl ;
                                                          #  "ticket","p1" → incident)
   │
   ├── "routing"  → RoutingAgent  → Claude → RoutingDiagnosis  (protocole, device,
   │                                          peer, root cause, evidence[], fix[])
   ├── "acl"      → ACLAgent      → Claude → ACLDiagnosis      (device, policy,
   │                                          decision permit/deny, root cause)
   └── "incident" → IncidentAgent → Claude → IncidentTicket    (sévérité, ticket_id,
                                              titre, devices affectés, étapes)
```

Chaque agent utilise **Pydantic** (`BaseModel`) pour définir strictement la forme de
sa réponse (champs obligatoires, types) — si Claude répond avec un JSON qui ne colle
pas au schéma, Pydantic lève une erreur plutôt que de laisser passer une réponse
malformée jusqu'à l'UI. C'est ce que veut dire "**LLM-as-judge**" et "**structured
output**" dans le jargon : on ne fait pas confiance à du texte libre, on force le
modèle à répondre dans un moule précis et vérifiable par du code classique.

**Sans `ANTHROPIC_API_KEY`** : `_has_anthropic()` renvoie `False`, chaque agent
bascule sur une réponse déterministe câblée en dur (pas d'IA du tout) — c'est le
"mode offline" mentionné dans le README. Avec la clé (notre cas), c'est un vrai appel
Claude à chaque fois.

### 2.4 Ce qui se passe précisément quand tu cliques "Run" sur un scénario Eval Harness

C'est la question exacte que tu poseras probablement à ton encadrant — voici la
réponse complète, étape par étape, vérifiée dans le code (`src/eval_harness.py`,
fonction `run_scenario`) :

```
1. Clic "Run" (UI)
     → fetch POST /api/mv/eval/run {"scenario_id": "bgp-001"}  (+ header X-API-Key)

2. Flask (multivendor_extensions.py) reçoit la requête
     → appelle eval_harness.run_scenario("bgp-001", agent="ai_command")

3. get_scenario("bgp-001")
     → charge la définition depuis src/scenarios.json : un "fault" injecté
       (ex. {"type":"bgp_peer_down","device":"de-fra-core-01","peer_ip":"10.200.0.13"})
       + les mots-clés attendus dans une bonne réponse (root cause / remediation)

4. synthesize_symptom(scenario)
     → transforme le fault JSON en une PHRASE en langage naturel, ex. :
       "The BGP peer uk-lon-core-01 (10.200.0.13) on device de-fra-core-01 is
        reporting state Idle. It was Established 5 minutes ago. Diagnose root
        cause and propose a remediation."
       (⚠️ ce symptôme est SYNTHÉTIQUE — rien n'a réellement cassé le pair BGP.
        L'agent va donc voir un pair BGP réellement UP quand il ira vérifier —
        et une bonne partie de sa "diagnose" consiste justement à confronter ce
        symptôme raconté à l'état réel qu'il observe.)

5. _invoke_agent_with_usage(agent, symptom)
     → appelle le MÊME code que le panneau "AI Command" (ou l'orchestrateur, selon
       l'agent choisi dans le sélecteur UI) : le LLM traduit en commande CLI,
       l'exécute réellement par SSH sur de-fra-core-01, puis explique le résultat.
       → c'est ici qu'un VRAI appel réseau + VRAI appel Claude a lieu.

6. keyword_score(agent_output, scenario)
     → note déterministe /10 : compte combien des mots-clés attendus
       (root_cause_keywords, remediation_keywords définis dans scenarios.json)
       apparaissent dans la réponse de l'agent. Pas d'IA ici, juste du texte.

7. llm_judge(agent_output, scenario)
     → SECOND appel Claude, différent du premier : on donne à Claude la question,
       les mots-clés attendus, ET la réponse de l'agent, et on lui demande de noter
       /10 avec un raisonnement. C'est ÇA le "LLM-as-judge" — un LLM qui évalue la
       réponse d'un autre appel LLM (ou du même modèle, réutilisé comme évaluateur
       indépendant). Bug connu ici : le budget de tokens de CE second appel est trop
       court (max_tokens=400) pour les diagnostics longs — voir SETUP_GUIDE.md Bug #3.

8. gait_audit.record(...)
     → écrit une ligne dans audit/gait_YYYY-MM-DD.jsonl : qui (eval_harness), quoi
       (run_scenario), sur quelle cible, prompt, résumé de la réponse, tokens
       consommés (les DEUX appels Claude cumulés : agent + juge).

9. Retour JSON complet → UI affiche keyword_score, llm_score, temps total, sortie brute
```

**Coût réel d'un clic** : 2 appels Claude (agent + juge) + 1 commande SSH réelle sur
un routeur. C'est pour ça que `usage.input`/`usage.output` dans la réponse comptent
les tokens des DEUX appels ensemble.

### 2.5 GAIT Audit — c'est quoi, et pourquoi ça existe

**GAIT = "Git AI Trail"** (le nom vient du projet qui a inspiré cette fonctionnalité,
NetClaw — rien à voir avec `git` le logiciel, c'est juste l'inspiration du nom).

**Le problème qu'il résout** : dès qu'un agent IA a le droit d'exécuter des commandes
sur de vrais routeurs (même en lab), il faut pouvoir répondre après-coup à *"qu'est-ce
que l'IA a fait, quand, avec quel prompt, quel résultat, et combien ça a coûté"* —
exactement comme un log d'audit de sécurité, mais pour des actions IA plutôt
qu'humaines.

**Comment c'est fait** (`src/gait_audit.py`) : un fichier **JSONL append-only**
(une ligne JSON par événement, jamais réécrit, jamais supprimé), un fichier par jour
(`audit/gait_2026-08-08.jsonl`), stocké directement dans le dépôt (gitignored). Chaque
ligne = `{id, ts, actor, action, target, prompt, response, tools_called, tokens,
status}`. **Append-only** = propriété importante pour un audit trail : on ne peut pas
"corriger" ou effacer une ligne passée, seulement en ajouter de nouvelles — c'est ce
qui rend le journal *fiable* pour la traçabilité (même logique qu'un log
d'audit financier ou qu'une blockchain, en beaucoup plus simple : ici la garantie
vient juste du fait que le code n'a pas de fonction "delete" ou "update", pas d'un
mécanisme cryptographique).

**Pourquoi le coût en tokens est stocké** : les appels LLM sont facturés au token, et
sans traçabilité par action, impossible de savoir après-coup si un incident a coûté
3 appels Claude ou 300 (dérive de coût, boucle infinie d'un agent...). C'est un
réflexe d'ingénierie de plateforme IA en production, pas juste une curiosité de démo.

### 2.6 `docker-compose.yml` et le rôle exact des 13 conteneurs

`network-lab/docker-compose.yml` (nom du projet compose : `dcn-lab`) définit
**13 services**, tous sur le réseau `lab-net` (`10.200.0.0/24`) :

| Conteneur | Image | Rôle |
|---|---|---|
| 10× routeurs (voir §1.1) | `frrouting/frr:v8.4.1` (build local) | Font du vrai BGP/OSPF/BFD entre eux. Exposent SSH (port hôte 2201-2210) et le CLI proxy HTTP (port hôte 8801-8810, port 8080 interne — **cassé sur cette machine**, voir UI_TESTING_LOG.md) |
| `influxdb` | `influxdb:2.7` | Base de données time-series — stocke la télémétrie (BGP/interfaces/CPU) collectée en continu |
| `grafana` | `grafana/grafana:10.4.0` | Dashboards de visualisation de ce qu'InfluxDB contient (accessible sur `:3000`, pas dans l'UI web principale) |
| `frr-telemetry` | build local (`network-lab/telemetry/`) | Un script Python qui se connecte en SSH à chacun des 10 routeurs toutes les 10s (`POLL_INTERVAL`), parse `show bgp/interfaces/...`, et écrit les métriques dans InfluxDB au format *line protocol* |

**Ce que `src/app.py` (le Flask sur `:5757`, l'UI que tu utilises) N'EST PAS** : il ne
tourne **pas** dans Docker dans cette configuration — c'est un process Python lancé
directement sur ta machine (`python app.py`, voir SETUP_GUIDE.md §6), qui elle-même
appelle `docker exec`/SSH vers les 13 conteneurs. Le Flask app et les conteneurs
réseau sont deux choses séparées qui communiquent via l'API Docker / SSH, pas via
`docker-compose.yml` (qui ne connaît que les 13 services réseau/télémétrie, pas l'UI).

---

## Partie 3 — Glossaire (termes logiciels/IA uniquement)

- **Agent** — Ici : une fonction qui encapsule un appel à un LLM avec un prompt
  système spécialisé et une forme de réponse attendue, pour une tâche précise
  (diagnostiquer du BGP, proposer un fix ACL...). Ce n'est PAS un agent au sens
  "boucle autonome qui décide elle-même de ses actions" (comme un agent Claude Code
  par exemple) — ici chaque agent répond une fois à une question et s'arrête.
- **Orchestrateur** — Le composant qui reçoit une requête en langage naturel, décide
  QUEL agent est le plus adapté (ici par mots-clés, pas par LLM), et lui délègue le
  travail. Dans ce projet : `pydantic_ai_orchestrator.py`.
- **LLM (Large Language Model)** — Le modèle de langage lui-même (ici : Claude,
  modèle `claude-haiku-4-5`, le plus petit/rapide de la famille Claude — choisi pour
  la vitesse et le coût, pas pour la capacité de raisonnement maximale).
- **LLM-as-judge** — Utiliser un (second) appel LLM pour ÉVALUER la qualité de la
  réponse d'un premier appel (ou d'un système non-LLM), au lieu (ou en plus) d'une
  évaluation par règles déterministes. Utilisé dans l'Eval Harness de ce projet.
- **Structured output / sortie structurée** — Forcer un LLM à répondre dans un format
  précis et typé (ici via Pydantic `BaseModel`) plutôt qu'en texte libre, pour que du
  code classique puisse fiablement lire les champs de la réponse sans re-parser du
  langage naturel.
- **Prompt système (system prompt)** — Les instructions de "rôle" données au LLM
  avant la question de l'utilisateur (ex. *"You are a network CLI expert for Juniper
  JunOS, Arista EOS, and FRRouting"*), invisibles pour l'utilisateur final,
  qui cadrent son comportement pour toute la conversation/requête.
- **Endpoint** — Une URL précise de l'API HTTP (ex. `POST /api/mv/eval/run`) que le
  frontend appelle pour déclencher une action côté serveur.
- **Driver (couche `drivers/`)** — Une classe qui sait traduire des commandes
  génériques ("donne-moi le résumé BGP") en la commande CLI exacte du bon vendeur
  (`show bgp summary` en FRR/EOS, `show bgp summary` aussi en Junos mais avec un
  parseur de sortie différent), puis normaliser la sortie dans un format commun. Le
  code au-dessus (health.py, l'UI...) n'a jamais besoin de savoir "si c'est du FRR ou
  du Junos" — le driver s'en charge.
- **Transport** (dans `drivers/`) — La couche encore en-dessous du driver : COMMENT on
  atteint physiquement l'appareil (`DockerExecTransport` = `docker exec` ;
  `SSHRunnerTransport` = vraie connexion SSH). Le driver ne s'en soucie pas non plus.
- **Adaptateur** (terme générique, pas un fichier précis de ce projet — c'est le
  terme que TU vas utiliser pour ton mémoire) — un composant qui traduit entre deux
  interfaces incompatibles sans que ni l'une ni l'autre n'ait à changer. Dans ce
  projet, `pydantic_ai_orchestrator.py` (qui n'appelle QUE Claude aujourd'hui) est
  précisément l'endroit où tu introduirais un **adaptateur multi-modèle** — une
  couche qui route vers Claude, GPT, un modèle local, etc. selon la tâche/le coût/la
  dispo, sans que le reste du code (`app.py`, l'UI, GAIT, l'Eval Harness) n'ait à
  savoir quel modèle a réellement répondu. C'est très exactement le trou que
  `_llm_query()` (dans `app.py`, différent de l'orchestrateur) essaie de combler
  aujourd'hui de façon ad-hoc et bogué (voir Bug "lenteur LLM" dans
  `UI_TESTING_LOG.md`) — un bon point de départ pour comparer "avant/après" dans ton
  mémoire.
- **Fallback (repli)** — Le comportement de secours quand le choix préféré échoue
  (ex. `_llm_query()` : Ollama → Docker Model Runner → Claude ; ou un agent qui
  bascule en réponse câblée en dur si `ANTHROPIC_API_KEY` est absente).
- **Token** — L'unité de facturation/mesure d'un LLM (environ 4 caractères anglais =
  1 token, en pratique). `usage.input`/`usage.output` dans les réponses de ce projet
  comptent les tokens envoyés (le prompt) et reçus (la réponse) à chaque appel.
- **Endpoint fail-closed** — Un design où l'absence de configuration (ex.
  `MVLAB_API_KEY` non définie) fait ÉCHOUER la requête (HTTP 503) plutôt que de
  l'autoriser par défaut — opposé de "fail-open". Choix de sécurité déjà présent dans
  ce projet pour toutes les routes mutantes.
- **JSONL (JSON Lines)** — Un fichier texte où chaque ligne est un objet JSON
  indépendant (par opposition à un unique gros tableau JSON) — permet d'ajouter des
  lignes (`append`) sans jamais relire/réécrire tout le fichier. Utilisé par GAIT.
- **Blueprint (Flask)** — Un module de routes Flask regroupées et enregistrées à part
  (`multivendor_extensions.py` → `mv_bp`), plutôt que tout empiler dans un seul
  fichier `app.py`. Un blueprint = un sous-ensemble organisé de endpoints.
- **RAG (Retrieval-Augmented Generation)** — Aller chercher (retrieval) des documents
  pertinents (ici : BM25 sur la doc CLI FRR/Junos/EOS, ou sur les configs sanitisées)
  AVANT d'appeler le LLM, pour lui donner du contexte factuel dans son prompt plutôt
  que de compter uniquement sur ce qu'il "sait" déjà. Utilisé par CLI Reference et
  Doc Search dans ce projet.
- **BM25** — Un algorithme de recherche textuelle classique (pré-LLM, des années 90),
  qui classe des documents par pertinence à une requête selon la fréquence des mots
  (pas de compréhension sémantique — une recherche par mots-clés pondérés,
  pas un "vecteur d'embedding"). Utilisé par CLI Reference.
- **Ring buffer (tampon circulaire)** — Une structure qui garde seulement les N
  derniers événements, en écrasant les plus anciens. Utilisé pour Syslog/SNMP dans ce
  projet (mémoire, pas persistant sur disque).
