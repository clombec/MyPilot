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

## À trancher en phase 2

- Planificateur : `discord.ext.tasks` dans le bot (prévu par la spec) pour le briefing et
  la relance ; l'export nocturne au même endroit.
- Session quotidienne Claude Code : `--resume <session_id>` stocké dans `DayLog`, à
  vérifier avec `claude --help` (version installée : 2.1.310).
- Service Windows : NSSM ou tâche planifiée au démarrage pour `web` et `bot`.
