# Lancer une évaluation multi-modèle — sans coder

Deux façons de faire, au choix. Les deux comparent les mêmes modèles (Claude +
modèles locaux Ollama) sur les mêmes scénarios d'incident réseau, et produisent
le même type de rapport lisible.

## Option 1 — Ligne de commande (Git Bash)

1. Ouvre Git Bash et va dans le dossier `src` du projet :
   ```
   cd /c/Users/hp/Desktop/multivendor-ai-network-lab/src
   ```
2. Lance une évaluation (exemple : 2 scénarios, 2 modèles, 3 répétitions) :
   ```
   ./venv/Scripts/python.exe run_evaluation_cli.py --scenarios bgp-001,ospf-001 --models claude-haiku-4-5,qwen2.5:3b --repeats 3
   ```
3. Sans aucun argument, ça lance **tout** (tous les scénarios × tous les
   modèles × 3 répétitions) :
   ```
   ./venv/Scripts/python.exe run_evaluation_cli.py
   ```
4. `--list` affiche les scénarios et modèles disponibles sans rien lancer :
   ```
   ./venv/Scripts/python.exe run_evaluation_cli.py --list
   ```

À la fin, le rapport s'affiche dans le terminal, et deux fichiers sont créés
dans `src/campaign_results/` : un `.jsonl` (données brutes, une ligne par
run) et un `-rapport.md` (le résumé lisible — c'est ce fichier que tu veux
pour ton mémoire).

## Option 2 — Interface web

1. Démarre le serveur (si pas déjà lancé), depuis `src/` :
   ```
   ./venv/Scripts/python.exe app.py
   ```
2. Ouvre http://localhost:5757 dans ton navigateur.
3. Sélectionne n'importe quel appareil dans la liste de gauche (nécessaire
   pour faire apparaître les onglets), puis clique sur l'onglet **🧪 Eval
   Harness**.
4. Coche les scénarios et modèles voulus (tout est coché par défaut), règle
   le nombre de répétitions, clique **▶ Lancer**.
5. Le rapport s'affiche à l'écran une fois terminé, avec des boutons
   **📋 Copier** et **💾 Télécharger .md**.

⚠️ Si le bouton **Lancer** affiche une erreur "unauthorized" : ouvre la
console du navigateur (touche `F12`) et tape une fois (la clé est dans
`src/.env`, ligne `MVLAB_API_KEY=`) :
```js
localStorage.setItem("mvlab_api_key", "TA_CLE_ICI")
```

## À savoir

- Les modèles locaux (Ollama) sont lents sur CPU — une campagne complète
  peut prendre plusieurs heures. Commence par un petit sous-ensemble
  (`--scenarios bgp-001 --models claude-haiku-4-5 --repeats 1`) pour
  vérifier que tout fonctionne avant de lancer une campagne complète.
- Le fichier `.jsonl` n'est jamais écrasé — chaque lancement crée un nouveau
  fichier horodaté.
