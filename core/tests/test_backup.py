"""
test_backup.py

Tests for export and restore, including the phase 1 acceptance criterion:
export then restore gives an identical database.
"""

from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core import services
from core.backup import (
    build_memory_markdown,
    export_all,
    restore_from_json,
    rotate_snapshots,
    serialize_database,
)
from core.importer import import_raw_lines
from core.models import (
    Briefing,
    DayLog,
    Event,
    Note,
    Observation,
    Project,
    Proposal,
    Rule,
    Task,
)


def populate() -> None:
    """Fill every model with a bit of data, including tricky relations."""
    drab = Project.objects.get(slug="drab")
    cabo = Project.objects.get(slug="cabo")
    tasks = import_raw_lines(["Stocks", "  commander gants", "  inventaire"], drab)
    late = Task.objects.create(title="Export Mnemos", project=drab, linked_project=cabo)
    # Parent pointing to a newer task (forward reference on restore).
    tasks[0].parent = late
    tasks[0].save()
    services.complete_task(tasks[1])
    event = Event.objects.create(
        title="Formation radioprotection",
        project=drab,
        start_date=date(2026, 11, 20),
        checklist=[{"label": "hôtel", "done": False}, {"label": "train", "done": True}],
    )
    event.tasks.set([tasks[2], late])
    DayLog.objects.create(date=date(2026, 10, 24), declared_focus="DrAB", energy="low")
    Note.objects.create(title="Règle facturation", text="…", tags=["mnemos"], project=drab)
    obs = services.add_observation("oublié mes clés", ["clés"])
    rule = Rule.objects.create(
        text="Rappeler les clés", trigger="jours cabinet", source_observation_ids=[obs.pk]
    )
    Proposal.objects.create(kind=Proposal.Kind.RULE, rule=rule, rationale="2 oublis")
    Briefing.objects.create(date=date(2026, 10, 25), content="1. Bonjour")


class RoundTripTests(TestCase):
    def test_export_then_restore_gives_identical_database(self) -> None:
        populate()
        before = serialize_database()
        with TemporaryDirectory() as tmp:
            paths = export_all([Path(tmp)], ref_date=date(2026, 10, 25))
            # Mess with the database, then restore.
            Task.objects.all().delete()
            Project.objects.filter(slug="dreb").delete()
            Task.objects.create(title="intrus")
            call_command("restore", str(paths[0]), no_backup=True, stdout=StringIO())
        self.assertEqual(serialize_database(), before)
        self.assertEqual(Event.objects.get().tasks.count(), 2)

    def test_restore_refuses_foreign_models_and_keeps_data(self) -> None:
        populate()
        before = serialize_database()
        bad = '[{"model": "auth.user", "pk": 1, "fields": {"username": "x"}}]'
        with self.assertRaises(ValueError):
            restore_from_json(bad)
        self.assertEqual(serialize_database(), before)

    def test_restore_command_reports_invalid_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("pas du json", encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command("restore", str(path), no_backup=True, stdout=StringIO())

    def test_export_contains_no_user_or_password(self) -> None:
        get_user_model().objects.create_user("moi", password="secret-très-long")
        dump = serialize_database()
        self.assertNotIn("auth.user", dump)
        self.assertNotIn("pbkdf2", dump)


class ExportFilesTests(TestCase):
    def test_export_writes_json_and_memory_in_each_dir(self) -> None:
        populate()
        with TemporaryDirectory() as a, TemporaryDirectory() as b:
            export_all([Path(a), Path(b)], ref_date=date(2026, 10, 25))
            for directory in (Path(a), Path(b)):
                self.assertTrue((directory / "snapshot_2026-10-25.json").is_file())
                memory = (directory / "memoire.md").read_text(encoding="utf-8")
                self.assertIn("oublié mes clés", memory)
                self.assertIn("## Profil", memory)

    def test_rotation_keeps_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            start = date(2026, 1, 1)
            for i in range(35):
                day = start + timedelta(days=i)
                (directory / f"snapshot_{day:%Y-%m-%d}.json").write_text("[]")
            removed = rotate_snapshots(directory, keep=30)
            remaining = sorted(p.name for p in directory.glob("snapshot_*.json"))
        self.assertEqual(len(removed), 5)
        self.assertEqual(len(remaining), 30)
        self.assertEqual(remaining[0], "snapshot_2026-01-06.json")

    def test_memory_lists_open_tasks_by_project(self) -> None:
        services.capture_task("relancer la banque", "clab")
        text = build_memory_markdown(date(2026, 10, 25))
        self.assertIn("### CLAB Services", text)
        self.assertIn("relancer la banque", text)

    def test_observations_are_append_only_in_export(self) -> None:
        Observation.objects.create(date=date(2026, 10, 1), text="a")
        self.assertIn('"text": "a"', serialize_database())
