# Sous-étude de validation — live vs synthétique

Ce guide explique comment lancer, seul et sans Claude Code, la sous-étude de
validation méthodologique recommandée par la revue externe : elle compare,
pour 5 scénarios, le score obtenu par chaque modèle quand on lui décrit une
panne de façon **synthétique** (texte fixe, comme dans la campagne
principale) contre le score obtenu quand on l'interroge sur l'**état réel**
des routeurs (requête `vtysh` effective via `docker exec`).

**Ne relance pas la campagne principale** (119-120 runs, 4 modèles, 10
scénarios) — elle est déjà validée. Cette sous-étude est indépendante,
ciblée sur 5 scénarios, et relit les scores de la campagne principale tels
quels depuis les fichiers déjà enregistrés (elle ne les recalcule jamais).

Deux façons de la lancer, au choix — les deux appellent exactement la même
fonction Python (`eval_harness.run_validation_substudy()`) et produisent le
même rapport ; choisis celle qui te convient, ou utilise la ligne de
commande en amont pour préparer les données et l'UI le jour J pour la
présentation visuelle.

---

## 0. Démarrer le lab (si ce n'est pas déjà fait)

Depuis Git Bash, à la racine du dépôt :

```bash
cd network-lab
docker compose up -d
```

Attends une vingtaine de secondes que BGP converge, puis vérifie :

```bash
docker exec de-fra-core-01 vtysh -c 'show bgp summary'
```

Tu dois voir les 6 voisins BGP de `de-fra-core-01` en état **Established**.
Si ce n'est pas encore le cas, réessaie la commande 10-15 secondes plus tard
(le lab prend un peu de temps à converger après un démarrage à froid).

La sous-étude interroge réellement `de-fra-core-01`, `de-fra-core-02` et
`nl-ams-core-01` — si le lab n'est pas démarré, chaque cellule affichera une
erreur de requête live (`live_query_error`) au lieu de planter : elle
retombe sur le texte synthétique pour ne pas bloquer, mais le résultat perd
alors tout son intérêt méthodologique. Assure-toi donc que le lab tourne
avant de lancer la sous-étude pour de vrai.

---

## 1. Option ligne de commande (Git Bash)

Depuis `src/` :

```bash
cd src
source venv/Scripts/activate
python run_evaluation_cli.py --validation-substudy --models claude-haiku-4-5
```

- `--models` accepte une liste séparée par des virgules parmi
  `claude-haiku-4-5,qwen2.5:3b,llama3.2:3b,phi3.5:3.8b`. Omis, les 4 tournent.
- `--scenarios` et `--repeats` sont ignorés par `--validation-substudy` (les
  5 scénarios live-capables et 1 run par cellule sont fixés par la
  sous-étude elle-même) — un message te le rappelle si tu les passes quand
  même.
- `--list` fonctionne normalement (affiche scénarios/modèles disponibles,
  aucun run lancé).

À la fin, le rapport s'affiche dans le terminal, et deux fichiers
apparaissent dans `src/campaign_results/`, jamais écrasés (nom horodaté) :
un `.jsonl` (une ligne par cellule scénario × modèle, avec la comparaison
synthétique/live déjà calculée dedans) et un `-rapport.md` (le résumé
lisible, même contenu que ce qui s'affiche à l'écran).

---

## 2. Option interface web (utile pour une démo live)

1. Démarre le serveur si besoin (depuis `src/`) :
   ```bash
   source venv/Scripts/activate
   python app.py
   ```
2. Ouvre `http://127.0.0.1:5757/demo/index.html`.
3. Sélectionne n'importe quel appareil dans la liste de gauche, puis
   l'onglet **🧪 Eval Harness**.
4. Fais défiler jusqu'à la section **🔬 Live vs Synthetic Validation**,
   juste sous le panneau Multi-Model Comparison. Toute l'interface de ce
   panneau (comme le reste de l'Eval Harness) est en anglais ; ce guide
   reste en français pour les explications, mais reprend les libellés
   anglais exacts entre guillemets.
5. Coche les modèles voulus (Claude est coché par défaut — voir la note de
   durée ci-dessous avant de cocher les modèles locaux pour une démo en
   direct), puis clique **▶ Run Validation**.
6. Le résultat s'affiche progressivement : deux tuiles de taux d'accord
   global ("Global agreement — keyword" / "Global agreement — LLM judge",
   colorées vert ≥70% · orange 40-69% · rouge <40%), un tableau détaillé par
   scénario × modèle (scores synthétique vs live, colorés par palier, avec
   ✅/❌ d'accord), un tableau d'accord par scénario, puis le rapport
   Markdown complet (en anglais lui aussi) avec **📋 Copy** et
   **💾 Download .md**.

⚠️ Si le clic renvoie une erreur "unauthorized" : même étape que pour le
reste de l'Eval Harness — `localStorage.setItem('MVLAB_API_KEY', '<clé de
src/.env>')` dans la console du navigateur (F12), puis recharge la page.
Voir `README_EVALUATION.md` et `SETUP_GUIDE.md` §7 pour le détail.

---

## 3. Durée à prévoir (important pour planifier une démo)

Mesurée sur cette machine (Dell Latitude 5400 / Windows 11), pour les
**5 cellules** (5 scénarios × 1 modèle) :

| Modèle | Durée pour 5 cellules | Recommandé pour une démo en direct |
|---|---|---|
| `claude-haiku-4-5` | **~45 secondes** (mesuré) | ✅ oui — c'est le choix par défaut de l'UI |
| `qwen2.5:3b` (Ollama, CPU) | **~10-11 minutes** (mesuré) | ⚠️ à lancer en amont, pas en direct |
| `llama3.2:3b` (Ollama, CPU) | ~12-15 minutes (estimé depuis la latence moyenne de la campagne principale) | ⚠️ à lancer en amont, pas en direct |
| `phi3.5:3.8b` (Ollama, CPU) | ~25-35 minutes (estimé, avec une variabilité connue et documentée dans `eval_harness.py` pour ce modèle) | ❌ non — lance-le la veille |

Les 4 modèles ensemble (comme le lance `--validation-substudy` sans
`--models`) : compte une bonne heure, séquentiel — c'est pour ça que le
bouton de l'UI ne coche que Claude par défaut.

**Pour la soutenance : coche uniquement `claude-haiku-4-5` dans l'UI** et
lance le bouton en direct (~1 minute, marge de sécurité incluse) — c'est
suffisant pour montrer le mécanisme et un résultat concret à l'écran.

Si tu veux montrer les 4 modèles comparés, lance la version complète
**avant** la soutenance en ligne de commande (elle peut tourner en arrière-
plan pendant que tu prépares le reste) :

```bash
python run_evaluation_cli.py --validation-substudy
```

puis reviens montrer le fichier `-rapport.md` déjà généré, ou recharge son
contenu dans l'UI en copiant le tableau — le fichier JSONL horodaté reste
disponible dans `src/campaign_results/` pour être rouvert à tout moment,
il n'est jamais écrasé par un nouveau lancement.

---

## 4. Lire le résultat

- **Taux d'accord global** : pourcentage de cellules (scénario × modèle) où
  le score synthétique et le score live tombent dans le même palier (🟢
  bon ≥7 · 🟡 moyen 4-6.9 · 🔴 faible <4) — les mêmes seuils que les
  couleurs utilisées partout ailleurs dans l'UI.
- **Un désaccord n'est pas automatiquement un problème** : le lab de ce
  projet est actuellement sain (aucun des 5 scénarios n'y est réellement
  injecté — voir `eval_harness.py`, ce lab "décrit" une panne sans la
  provoquer). Si le modèle reconnaît correctement, en mode live, que le
  routeur va bien — alors qu'on lui avait décrit une panne en mode
  synthétique — c'est un **bon** comportement du modèle, pas une erreur,
  même si ça compte comme un désaccord dans le tableau.
- **Le score juge (LLM) est parfois marqué `—`** : c'est une limitation déjà
  documentée dans `eval_harness.aggregate_results()` — le juge renvoie
  parfois une valeur non numérique et cette ligne est alors exclue du calcul
  plutôt que faussée. Le taux d'accord basé sur le score **mots-clés** est
  plus complet pour cette raison ; préfère-le si tu dois n'en citer qu'un.
- Le rapport Markdown (section **Limitations**) rappelle ces points
  automatiquement à chaque lancement — tu peux le citer tel quel dans ta
  soutenance.

---

## 5. En cas de souci

- **`live_query_error` dans le résultat / raw output vide** : le lab n'est
  pas démarré ou pas encore convergé — reviens à l'étape 0.
- **Erreur "unauthorized" côté UI uniquement** : clé API manquante dans ce
  navigateur — voir §2 ci-dessus.
- **`Could not find platform independent libraries <prefix>`** au démarrage
  du script : avertissement Python cosmétique, sans effet sur le
  fonctionnement — ignore-le (déjà présent sur les autres scripts de ce
  projet).
- **Un modèle Ollama semble bloqué** : normal sur CPU, voir le tableau de
  durées ci-dessus avant de conclure à un plantage — laisse tourner.
