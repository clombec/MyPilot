"""
0002_seed_initial_data.py

Initial data: the five projects, the first profile and the first briefing spec.
Texts are copied here on purpose so the migration never depends on later code.
"""

from django.db import migrations

PROJECTS = [
    {
        "slug": "drab",
        "name": "DrAB",
        "priority": 1,
        "description": (
            "Cabinet dentaire de l'épouse : tout le non-médical (administratif, finances, "
            "RH, IT, travaux, achats, formation, planning)."
        ),
        "fixed_weekday": None,
    },
    {
        "slug": "aure",
        "name": "Aure Tech",
        "priority": 2,
        "description": "Société NTIC, salarié, travail firmware (2 jours/semaine, 3 demandés).",
        "fixed_weekday": None,
    },
    {
        "slug": "clab",
        "name": "CLAB Services",
        "priority": 3,
        "description": (
            "Entreprise personnelle : contrats bancaires, compta Indy, factures Kolecto, contrats."
        ),
        "fixed_weekday": None,
    },
    {
        "slug": "cabo",
        "name": "CaBO",
        "priority": 4,
        "description": (
            "Logiciel maison qui lit OrthoAdvance pour le cabinet DrAB, à rendre générique "
            "(module Mnemos : LLM locale via Ollama)."
        ),
        "fixed_weekday": None,
    },
    {
        "slug": "dreb",
        "name": "DrEB",
        "priority": 5,
        "description": "Autre cabinet dentaire : gestion IT.",
        "fixed_weekday": 1,  # Tuesday
    },
]

INITIAL_PROFILE = """\
# Profil

## Activités (par priorité)
1. DrAB : cabinet dentaire de mon épouse, tout le non-médical (administratif, finances,
   RH, IT, travaux, achats, formation, planning).
2. Aure Tech : salarié, firmware. 2 jours par semaine (3 demandés).
3. CLAB Services : mon entreprise (banque, compta Indy, factures Kolecto, contrats).
4. CaBO : logiciel maison qui lit OrthoAdvance, à rendre générique (module Mnemos).
5. DrEB : gestion IT du cabinet, le mardi (bientôt un mardi sur deux).

## Rythme
- Les jours de travail changent d'une semaine à l'autre : ne rien présupposer, demander.
- Je travaille aussi le samedi.

## Cibles hebdomadaires (jours)
- Aure Tech : 2
- DrEB : 1 (le mardi)
- Le reste : à préciser.
"""

INITIAL_BRIEFING_SPEC = """\
# Format du briefing du matin

Sections numérotées, dans cet ordre :
1. Une phrase de situation : le jour, ce qui est prévu s'il y a quelque chose.
2. Rappels du jour : les consignes qui s'appliquent aujourd'hui (ex. les clés).
3. Échéances et événements des 7 prochains jours.
4. Une question : « Qu'est-ce que tu fais aujourd'hui, combien de temps, quelle énergie ? »
5. 2 ou 3 éléments de la boîte de réception à clarifier, avec une question courte chacun.

Contraintes :
- 1 200 caractères maximum.
- Une section vide est omise, sans renuméroter les autres.
- Tutoiement, ton direct.
"""


def seed(apps, schema_editor):
    """Create the initial projects, profile and briefing spec."""
    Project = apps.get_model("core", "Project")
    ProfileVersion = apps.get_model("core", "ProfileVersion")
    BriefingSpecVersion = apps.get_model("core", "BriefingSpecVersion")
    for data in PROJECTS:
        Project.objects.update_or_create(slug=data["slug"], defaults=data)
    if not ProfileVersion.objects.exists():
        ProfileVersion.objects.create(
            content=INITIAL_PROFILE, status="accepted", rationale="Version initiale"
        )
    if not BriefingSpecVersion.objects.exists():
        BriefingSpecVersion.objects.create(
            content=INITIAL_BRIEFING_SPEC,
            source="initial",
            status="accepted",
            instruction="Version initiale (CLAUDE_pilotage.md §8)",
        )


def unseed(apps, schema_editor):
    """Remove the seeded projects (versions are kept)."""
    Project = apps.get_model("core", "Project")
    Project.objects.filter(slug__in=[p["slug"] for p in PROJECTS]).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
