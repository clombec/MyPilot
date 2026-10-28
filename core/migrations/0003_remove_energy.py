"""
0003_remove_energy.py

Drop the energy notion: the three energy fields, and the energy question in the
initial briefing spec (only if that seeded version is still the active one).
"""

from django.db import migrations

OLD_QUESTION = "combien de temps, quelle énergie ?"
NEW_QUESTION = "combien de temps ?"


def drop_energy_question(apps, schema_editor):
    """Remove the energy question from the active spec if it is the initial one."""
    BriefingSpecVersion = apps.get_model("core", "BriefingSpecVersion")
    active = (
        BriefingSpecVersion.objects.filter(status="accepted")
        .order_by("-created_at", "-id")
        .first()
    )
    if active and active.source == "initial" and OLD_QUESTION in active.content:
        active.content = active.content.replace(OLD_QUESTION, NEW_QUESTION)
        active.save(update_fields=["content"])


class Migration(migrations.Migration):
    dependencies = [("core", "0002_seed_initial_data")]

    operations = [
        migrations.RemoveField(model_name="daylog", name="energy"),
        migrations.RemoveField(model_name="task", name="energy"),
        migrations.RemoveField(model_name="worksession", name="perceived_energy"),
        migrations.RunPython(drop_energy_question, migrations.RunPython.noop),
    ]
