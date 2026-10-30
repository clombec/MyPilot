# Décisions techniques

Format : décision, puis raison en une ou deux lignes. Les plus récentes en bas.

## Phase 1 (2026-10-25)

1. **django-ninja plutôt que DRF.** Plus léger, schémas typés (pydantic), documentation
   `/api/docs` gratuite. Le besoin est du CRUD simple : DRF n'apporterait rien de plus.
2. **Projet Django `config/`, apps `core`, `api`, `web`** (puis `bot`, `agent` en phase 2).
   `core` porte les modèles et toute la logique de données ; `api` et `web` ne sont que des
   couches HTTP.
3. **Logique dans plusieurs modules de `core`** plutôt qu'un seul `services.py` :
   `services.py` (opérations de base), `importer.py`, `backup.py`, `snapshot.py`. Chaque
   module reste lisible et testable seul. Aucun ne contient de priorisation (§2).
4. **SQLite en WAL + `transaction_mode=IMMEDIATE` + délai de 20 s.** Le bot et le web
   écriront dans la même base : IMMEDIATE évite les erreurs « database is locked » au
   moment de passer d'une lecture à une écriture.
5. **Base dans `data/` (hors Git, hors cloud)**, chemin surchargeable par `DATABASE_PATH`.
6. **Données initiales par migration** (`0002_seed_initial_data`) : les 5 projets
   (slugs `drab`, `aure`, `clab`, `cabo`, `dreb` ; DrEB fixé au mardi), le profil v1 et la
   spec de briefing v1 (§8), tous deux `accepted`. Les textes sont copiés dans la
   migration pour qu'elle ne dépende jamais du code futur.
7. **Version active = dernière version `accepted`** (profil, spec de briefing). Revenir en
   arrière = ré-accepter une ancienne version (copie) : l'historique n'est jamais réécrit.
8. **`Proposal` générique** avec un champ `kind` et une FK optionnelle par cible
   (profil, consigne, spec de briefing, tâche). L'acceptation est faite par
   `services.decide_proposal`, partagée par l'API, le web et (phase 2) les boutons Discord.
   Un abandon de tâche accepté passe la tâche en `dropped`, jamais de suppression.
9. **`AuditLog.undo_data`** : chaque écriture stockera l'état nécessaire à l'annulation
   (`/annuler`). L'import brut y enregistre déjà la liste des tâches créées.
10. **`DayLog.agent_session_id`** : prévu pour la session Claude Code du jour (§5 bis),
    non exposé par l'API.
11. **Export limité à l'app `core`**, dans un ordre de dépendances fixe. Pas de comptes
    Django (empreintes de mots de passe), pas de sessions. La restauration refuse tout
    fichier contenant un autre modèle, supprime puis recharge dans une seule transaction,
    et sauvegarde d'abord l'état courant dans `data/backups/`.
12. **Import brut** : titre = ligne sans espaces ni puce de liste ; `raw_text` = ligne
    exacte, indentation comprise. Parent provisoire seulement pour un niveau 1 sous une
    ligne de niveau 0, si le fichier ne mélange pas tabulations et espaces et si la ligne
    ne revient pas à une indentation inconnue. Les niveaux plus profonds restent à plat
    (un seul niveau de chantier), avec `niveau` et `sous` dans `notes`. Lecture UTF-8,
    puis Windows-1252 en repli.
13. **Interface web protégée par la connexion Django** (`createsuperuser`), en plus de
    Tailscale. Le jeton d'API n'est pas utilisé côté navigateur.
14. **HTMX servi localement** (`web/static/web/htmx.min.js`, v2.0.4) : aucune dépendance à
    un CDN, l'interface marche même sans accès Internet.
15. **waitress + whitenoise** pour l'usage réel : waitress fonctionne sous Windows et Mac
    (gunicorn non), whitenoise sert les fichiers statiques sans serveur web séparé.
16. **Filtre par étiquette des observations fait en Python** : SQLite ne gère pas la
    recherche dans une liste JSON. Volume faible, sans conséquence.
17. **Ruff** (règles E, W, F, I, B, UP ; 99 colonnes) pour PEP 8 et l'ordre des imports.

## Phase 2 (2026-10-25)

18. **Options de Claude Code vérifiées sur la version installée (2.1.310)** par `claude --help`
    et un essai réel. Retenu : `--tools ""` (coupe tous les outils intégrés, plus sûr qu'une
    liste d'interdictions qui oublierait un outil), `--permission-mode dontAsk` +
    `--permission-prompts none`, `--strict-mcp-config`, `--allowedTools mcp__clabpilot`,
    `--setting-sources project` (ni réglages, ni hooks, ni mémoire de l'utilisateur),
    `--disable-slash-commands`, `--no-chrome`. `--max-turns` n'est plus documenté mais reste
    accepté. Le message passe par l'entrée standard (pas de limite de longueur de la ligne de
    commande Windows).
19. **Dossier de travail de l'agent hors du dépôt** (`~/.clabpilot/agent_workspace`) : Claude
    Code lit les CLAUDE.md des dossiers parents, l'agent aurait sinon reçu les règles de
    développement. La source versionnée reste `agent_workspace/CLAUDE.md`, copiée au
    démarrage ; `mcp_config.json` est généré (chemins absolus propres à la machine). Une
    valeur dans le dépôt est remplacée par le défaut, avec un avertissement.
20. **Outils de l'agent dans `core/agent_tools.py`**, en Python simple, testables sans Claude ;
    `agent/mcp_server.py` ne fait que les déclarer. Paquet `mcp` v2 (`MCPServer`, ex-FastMCP) :
    les outils synchrones tournent dans un thread, donc l'ORM Django s'y utilise normalement.
21. **Annulation générique** : chaque écriture stocke dans `AuditLog.undo_data` une liste
    d'opérations (`delete`, `set_fields` avec liens M2M). `/annuler` applique la dernière
    entrée de l'agent non annulée. Un changement de format annulé passe la nouvelle version
    en `rejected` : la précédente redevient active, l'historique reste complet.
22. **Garde-fou minimal contre les secrets dans le code** (motifs « mdp : xxx »,
    « password=xxx », clés d'API) en plus de la consigne de l'agent. « Noter le mdp dans le
    carnet » reste permis. Les données patient restent du ressort de l'agent (aucune
    détection fiable possible par motif).
23. **Session du jour** : `DayLog.agent_session_id`, reprise par `--resume`. Le briefing
    ouvre une session neuve qui devient celle du jour. Session introuvable → nouvelle
    session, avec les 10 derniers échanges des 24 h. Chaque message reçoit un « contexte
    frais » (heure, journée déclarée, tâches et observations modifiées depuis le dernier
    échange).
24. **Planificateur dans le bot** (`discord.ext.tasks`) : briefing, relance, export nocturne
    (`EXPORT_TIME`, défaut 03:00). Pas de cron : un seul processus à surveiller. Briefing
    manqué (PC éteint) envoyé au démarrage s'il est avant midi.
25. **La relance de 10 h n'appelle pas Claude** : texte fixe, coût nul.
26. **Boutons de validation gérés par `on_interaction`** sur un identifiant
    `clabpilot:proposal:<id>:<accept|reject>` : ils marchent encore après un redémarrage.
27. **Un seul processus `claude` à la fois** (verrou asyncio dans le backend), une
    nouvelle tentative au plus sur échec ou délai dépassé ; jamais sur limite d'usage.
28. **Démarrage automatique par tâches planifiées Windows** (et non un service) : un
    service tournerait sous un autre compte, non connecté à Claude Code.
29. **Pages web Propositions et Journal** ajoutées dès la phase 2 : même effet que les
    boutons Discord et `/annuler`, utile si Discord est indisponible.

## Pour la phase 3

- Revue hebdomadaire plus riche, résumés glissants du journal.
- Pages web Événements (checklists cochables) et Mémoire (profil, consignes, versions).
- Mode Mac en lecture seule, `import_json`.
