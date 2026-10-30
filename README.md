# ClabPilot

Coffre de données personnel + agent Claude + bot Discord pour piloter cinq activités
(DrAB, Aure Tech, CLAB Services, CaBO, DrEB). Spécification complète :
[`CLAUDE_clabpilot.md`](CLAUDE_clabpilot.md). Conventions de code : [`CLAUDE.md`](CLAUDE.md).

**Principe directeur** : le code enregistre, partage et montre les données. Il ne planifie
rien. Toute la gestion (priorités, propositions, rappels, clarification) est faite par
Claude, via Claude Code exécuté en local (`claude -p`).

## État d'avancement

| Phase | Contenu | État |
|---|---|---|
| 1 | Modèles, API, interface minimale, `import_raw`, `export_snapshot` / `restore`, tests | ✅ fait |
| 2 | Bot Discord, serveur MCP `clabpilot`, `ClaudeCodeBackend`, briefing, `/annuler`, propositions | ✅ fait |
| 3 | Revue hebdo, résumés, mode Mac, `import_json` | à faire |

Le catalogue des outils de l'agent est suivi dans `outils_agent.xlsx` (les 22 outils prévus
existent, plus `list_projects` et `get_briefing_spec`).

## Arborescence

```
Pilot/
├── config/            réglages Django, URLs racine, WSGI
├── core/              modèles (§4), services de données, import, export, snapshot,
│   │                  outils de l'agent (agent_tools.py), annulation (undo.py)
│   ├── management/commands/   import_raw, export_snapshot, restore, run_bot, agent_check
│   └── tests/
├── api/               API REST (django-ninja), jeton Bearer
├── web/               interface web mobile (gabarits Django + HTMX)
├── docs/DECISIONS.md  décisions techniques
├── bot/               bot Discord : commandes, salons, boutons, planificateur
├── agent/             serveur MCP `clabpilot`, ClaudeCodeBackend, service (sessions)
├── agent_workspace/   CLAUDE.md = prompt système de l'agent (source versionnée)
└── scripts/windows/   démarrage automatique (tâches planifiées)
```

## Installation (Windows, PC fixe)

Prérequis : Python 3.12+.

```powershell
git clone <url> Pilot
cd Pilot
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env      # puis remplir DJANGO_SECRET_KEY, API_TOKEN, ALLOWED_HOSTS, EXPORT_DIRS
.venv\Scripts\python manage.py migrate          # crée la base + les 5 projets + profil/briefing v1
.venv\Scripts\python manage.py createsuperuser  # compte de connexion à l'interface web
.venv\Scripts\python manage.py collectstatic --noinput
```

Sur Mac/Linux : `python3 -m venv .venv` puis `.venv/bin/python …`.

Générer des secrets :

```powershell
.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(48))"
```

La base vit dans `data/clabpilot.sqlite3` (mode WAL). **Ne jamais la placer dans un dossier
synchronisé** (OneDrive, Google Drive) : seuls les exports y vont.

## Lancement

Développement :

```powershell
.venv\Scripts\python manage.py runserver
```

Usage réel, à l'écoute de l'interface Tailscale uniquement (remplacer par l'IP Tailscale du
PC, visible avec `tailscale ip -4`, et l'ajouter à `ALLOWED_HOSTS`) :

```powershell
.venv\Scripts\waitress-serve --listen=100.x.y.z:8000 config.wsgi:application
```

- Interface web : `http://100.x.y.z:8000/` (connexion avec le compte `createsuperuser`).
- API : `http://100.x.y.z:8000/api/` — documentation interactive sur `/api/docs`.
  Chaque appel envoie `Authorization: Bearer <API_TOKEN>`. Si `API_TOKEN` est vide, tout
  est refusé.
- Admin Django (secours) : `/admin/`.

### Bot Discord et agent

Prérequis : §15 de la spec (serveur Discord, bot avec l'intent *Message Content*, IDs dans
`.env`), et Claude Code installé **et connecté** sur le PC avec ton abonnement (`claude`,
puis `/login`).

```powershell
.venv\Scripts\python manage.py agent_check   # 1 appel réel, lecture seule : vérifie toute la chaîne
.venv\Scripts\python manage.py run_bot       # lance le bot, le planificateur et l'agent
```

Au démarrage, `run_bot` copie `agent_workspace/CLAUDE.md` et génère `mcp_config.json` dans
`AGENT_WORKDIR` (par défaut `~/.clabpilot/agent_workspace`). Ce dossier doit être **hors du
dépôt** : Claude Code lit les CLAUDE.md des dossiers parents, et les règles de développement
ne doivent pas atteindre l'agent. Une valeur dans le dépôt est remplacée par le défaut, avec
un avertissement dans le journal.

Modifier le comportement de l'agent = modifier `agent_workspace/CLAUDE.md`, puis redémarrer
le bot. Aucun code à toucher.

### Démarrage automatique (Windows)

Deux tâches planifiées, lancées à l'ouverture de session et relancées en cas d'arrêt :

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install_tasks.ps1 -ListenAddress 100.x.y.z:8000
powershell -ExecutionPolicy Bypass -File scripts\windows\install_tasks.ps1 -Remove   # désinstaller
```

Elles tournent sous ton compte (c'est lui qui est connecté à Claude Code). Le PC doit être
allumé et la session ouverte pour que le briefing parte. Si le PC était éteint à l'heure du
briefing, il est envoyé au démarrage du bot (avant midi).

## Discord

| Salon | Rôle |
|---|---|
| `#briefing` | point du matin (`BRIEFING_TIME`), relance douce à `BRIEFING_NUDGE_TIME` sans réponse |
| `#agent` | dialogue libre avec l'agent (une conversation par jour, reprise toute la journée) ; accepte aussi les webhooks listés dans `DISCORD_AGENT_WEBHOOK_IDS` |
| `#capture` | chaque message devient une tâche brute (`clab: texte` → projet CLAB) |
| `#propositions` | consignes, profil, format, abandons proposés par l'agent : Accepter / Refuser |

| Commande | Claude ? | Effet |
|---|---|---|
| `/note <texte>` | non | ajoute une observation |
| `/capture <texte> [projet]` | non | tâche brute |
| `/statut` | non | compteurs, échéances, propositions en attente |
| `/annuler` | non | annule la dernière écriture de l'agent |
| `/fait <n°>` | non | clôt la tâche n° (avec du texte : l'agent retrouve la tâche) |
| `/briefing-format` | non | spec actuelle + 5 versions, menu pour revenir en arrière |
| `/now <durée> [contexte]` | oui | 2 ou 3 options justifiées |
| `/jour <focus> [heures]` | oui | enregistre la journée, l'agent propose un plan |
| `/clarifie [n]` | oui | l'agent prend n éléments bruts et pose ses questions |
| `/semaine` | oui | revue de la semaine |
| `/briefing` | oui | regénère le briefing dans `#briefing` |

Le bot ignore tout autre utilisateur que `DISCORD_OWNER_ID` et tout autre salon. Si Claude
est indisponible ou la limite d'usage atteinte, un message clair s'affiche ; les commandes
sans Claude continuent de marcher et le message saisi est conservé.

### Sécurité de l'agent

L'agent est lancé avec `--tools ""` (aucun outil intégré : ni shell, ni fichiers, ni web),
`--strict-mcp-config` (seul le serveur `clabpilot`), `--permission-mode dontAsk` et
`--permission-prompts none` (tout ce qui n'est pas autorisé d'avance est refusé). Aucun mode
de contournement des permissions. `ANTHROPIC_API_KEY`, le jeton Discord, `API_TOKEN` et la
clé Django sont retirés de son environnement (`AGENT_AUTH=subscription`). Chaque écriture
est journalisée (`AuditLog`) et annulable (`/annuler`, ou page Journal du web).

### Usage et limites

Connecté avec l'abonnement, `claude -p` puise dans les limites de l'abonnement (fenêtre de
5 heures et plafond hebdomadaire partagés avec claude.ai et le développement). Vérifié le
25/10/2026 sur la page d'aide Anthropic « Use the Claude Agent SDK with your Claude plan »
(mise à jour du 7 octobre 2026). Un appel ordinaire prend 2 à 20 s.

## Commandes

```powershell
# Tas brut : chaque ligne non vide devient une tâche inbox/raw
.venv\Scripts\python manage.py import_raw todo.txt --project drab
.venv\Scripts\python manage.py import_raw todo.txt --dry-run     # aperçu sans écrire

# Export : snapshot_YYYY-MM-DD.json + memoire.md dans chaque dossier de EXPORT_DIRS
# (30 JSON gardés). Sans EXPORT_DIRS : data/exports/
.venv\Scripts\python manage.py export_snapshot
.venv\Scripts\python manage.py export_snapshot --dir D:\autre\dossier

# Restauration (l'état actuel est d'abord sauvegardé dans data/backups/)
.venv\Scripts\python manage.py restore chemin\snapshot_2026-10-25.json
```

L'export ne contient que les données `core` : jamais les comptes Django (mots de passe),
ni les sessions.

## API

| Point d'accès | Opérations |
|---|---|
| `/api/projects` | CRUD (par `slug`) |
| `/api/tasks` | CRUD, filtres `project` (`none` = sans projet), `status`, `clarity`, `due_before`, `q`, `order`, `limit` ; `POST /api/tasks/{id}/done` |
| `/api/events` | CRUD, filtres `start`, `end` ; `checklist` et `task_ids` |
| `/api/notes` | CRUD, recherche `q` |
| `/api/observations` | lecture, ajout ; seul `pinned` est modifiable (ajout seul) |
| `/api/rules` | CRUD (une consigne créée par l'utilisateur est active d'emblée) |
| `/api/days` | liste, `GET`/`PUT /api/days/YYYY-MM-DD` |
| `/api/snapshot` | snapshot Markdown pour l'agent (`scope=full|light`) |
| `/api/briefings` | lecture |
| `/api/proposals` | liste, `POST /api/proposals/{id}/accept|reject` |

`raw_text` n'est jamais modifiable après création.

## Tests et qualité

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python manage.py test
.venv\Scripts\ruff check .
.venv\Scripts\ruff format .
```

Tests réels de l'agent (consomment un peu d'usage, base temporaire vide) :

```powershell
$env:CLABPILOT_LIVE_AGENT="1"; .venv\Scripts\python manage.py test agent.tests.test_live
```

Critères d'acceptation de la phase 1 couverts par les tests :
- `core.tests.test_importer.ImportAcceptanceTests` : 150 lignes → 150 tâches `inbox`/`raw`,
  texte d'origine intact.
- `core.tests.test_backup.RoundTripTests` : export puis restauration → base identique.

Critères d'acceptation de la phase 2 :
- tests unitaires : autre utilisateur ignoré (`bot.tests.test_handlers.AuthorizationTests`),
  `/note` `/capture` `/statut` sans Claude, consigne active après validation, changement de
  format puis `/annuler` qui restaure la version précédente
  (`core.tests.test_agent_tools.UndoTests`), ligne de commande sans outil ni contournement
  (`agent.tests.test_agent.CommandLineTests`) ;
- tests réels (`agent.tests.test_live`) : lecture de fichier et commande refusées ;
- scénario réel déroulé le 25/10/2026 sur une base jetable : 2ᵉ oubli de clés → consigne
  proposée ; après validation, briefing d'un jour « cabinet » avec le rappel des clés ;
  `/now 1h faible` → 3 options justifiées ; « mets les échéances en premier et raccourcis »
  → nouvelle spec appliquée au briefing suivant, puis `/annuler` → version précédente active.
