# Pilotage

Coffre de données personnel + agent Claude + bot Discord pour piloter cinq activités
(DrAB, Aure Tech, CLAB Services, CaBO, DrEB). Spécification complète :
[`CLAUDE_pilotage.md`](CLAUDE_pilotage.md). Conventions de code : [`CLAUDE.md`](CLAUDE.md).

**Principe directeur** : le code enregistre, partage et montre les données. Il ne planifie
rien. Toute la gestion (priorités, propositions, rappels, clarification) est faite par
Claude (Claude Code local, phase 2).

## État d'avancement

| Phase | Contenu | État |
|---|---|---|
| 1 | Modèles, API, interface minimale, `import_raw`, `export_snapshot` / `restore`, tests | ✅ fait |
| 2 | Bot Discord, serveur MCP `pilotage`, `ClaudeCodeBackend`, briefing | à faire |
| 3 | Revue hebdo, résumés, mode Mac, `import_json` | à faire |

Le catalogue des outils de l'agent (phase 2) est suivi dans `outils_agent.xlsx`.

## Arborescence

```
Pilot/
├── config/            réglages Django, URLs racine, WSGI
├── core/              modèles (§4), services de données, import, export, snapshot
│   ├── management/commands/   import_raw, export_snapshot, restore
│   └── tests/
├── api/               API REST (django-ninja), jeton Bearer
├── web/               interface web mobile (gabarits Django + HTMX)
├── docs/DECISIONS.md  décisions techniques
├── bot/               (phase 2) bot Discord + planificateur
├── agent/             (phase 2) serveur MCP + ClaudeCodeBackend
└── agent_workspace/   (phase 2) dossier de travail de l'agent : CLAUDE.md, mcp_config.json
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

La base vit dans `data/pilotage.sqlite3` (mode WAL). **Ne jamais la placer dans un dossier
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

Le service Windows (bot + web toujours actifs) sera mis en place en phase 2.

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

Critères d'acceptation de la phase 1 couverts par les tests :
- `core.tests.test_importer.ImportAcceptanceTests` : 150 lignes → 150 tâches `inbox`/`raw`,
  texte d'origine intact.
- `core.tests.test_backup.RoundTripTests` : export puis restauration → base identique.
