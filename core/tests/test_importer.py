"""
test_importer.py

Tests for the raw import (`import_raw`), including the phase 1 acceptance
criterion: 150 lines give 150 raw inbox tasks without loss.
"""

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.importer import import_raw_lines, parse_lines
from core.models import AuditLog, Project, Task


def _make_pile(n: int) -> list[str]:
    """Build a realistic raw pile of n non-empty lines with blocks and blanks."""
    lines = []
    i = 0
    while i < n:
        lines.append(f"Chantier {i} : gestion des stocks é à ç")
        i += 1
        for j in range(3):
            if i >= n:
                break
            lines.append(f"    - sous-tâche {i}.{j} (appeler le fournisseur)")
            i += 1
        lines.append("")
    return lines


class ImportAcceptanceTests(TestCase):
    def test_150_lines_create_150_raw_inbox_tasks(self) -> None:
        lines = _make_pile(150)
        non_empty = [ln for ln in lines if ln.strip()]
        self.assertEqual(len(non_empty), 150)

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pile.txt"
            path.write_text("\n".join(lines), encoding="utf-8")
            call_command("import_raw", str(path), project="drab", stdout=StringIO())

        tasks = list(Task.objects.order_by("id"))
        self.assertEqual(len(tasks), 150)
        drab = Project.objects.get(slug="drab")
        for task, original in zip(tasks, non_empty, strict=True):
            self.assertEqual(task.status, Task.Status.INBOX)
            self.assertEqual(task.clarity, Task.Clarity.RAW)
            self.assertEqual(task.raw_text, original)  # verbatim, indentation included
            self.assertEqual(task.project, drab)
            self.assertEqual(task.created_by, "import")
        self.assertEqual(AuditLog.objects.filter(tool="import_raw").count(), 1)


class ParseTests(TestCase):
    def test_bullets_are_removed_from_title_only(self) -> None:
        parsed = parse_lines(["- acheter gants", "* relancer banque", "1. appeler Indy"])
        self.assertEqual(
            [p.title for p in parsed.lines],
            ["acheter gants", "relancer banque", "appeler Indy"],
        )

    def test_one_level_block_gets_provisional_parent(self) -> None:
        tasks = import_raw_lines(["Gestion des stocks", "  commander gants", "  inventaire"])
        parent, child1, child2 = tasks
        self.assertIsNone(parent.parent)
        self.assertEqual(child1.parent, parent)
        self.assertEqual(child2.parent, parent)
        self.assertEqual(child1.notes, 'niveau: 1, sous: "Gestion des stocks"')
        self.assertEqual(parent.notes, "")

    def test_second_level_stays_flat_but_is_noted(self) -> None:
        tasks = import_raw_lines(["A", "  B", "    C"])
        self.assertIsNone(tasks[2].parent)
        self.assertEqual(tasks[2].notes, 'niveau: 2, sous: "B"')

    def test_mixed_tabs_and_spaces_give_no_parent(self) -> None:
        tasks = import_raw_lines(["A", "\tB", "    C"])
        self.assertTrue(all(t.parent is None for t in tasks))

    def test_dedent_to_unknown_level_is_ambiguous(self) -> None:
        tasks = import_raw_lines(["A", "    B", "  C"])
        self.assertEqual(tasks[1].parent, tasks[0])
        self.assertIsNone(tasks[2].parent)

    def test_indented_first_line_has_no_parent(self) -> None:
        tasks = import_raw_lines(["   A", "B"])
        self.assertTrue(all(t.parent is None for t in tasks))

    def test_blank_lines_are_skipped(self) -> None:
        self.assertEqual(len(import_raw_lines(["", "A", "   ", "B", ""])), 2)


class CommandTests(TestCase):
    def test_unknown_project_is_refused(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pile.txt"
            path.write_text("A\n", encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command("import_raw", str(path), project="inconnu")
        self.assertEqual(Task.objects.count(), 0)

    def test_cp1252_file_is_read(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pile.txt"
            path.write_bytes("Préparer la comptabilité\n".encode("cp1252"))
            call_command("import_raw", str(path), stdout=StringIO())
        self.assertEqual(Task.objects.get().title, "Préparer la comptabilité")

    def test_dry_run_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pile.txt"
            path.write_text("A\n  B\n", encoding="utf-8")
            out = StringIO()
            call_command("import_raw", str(path), dry_run=True, stdout=out)
        self.assertEqual(Task.objects.count(), 0)
        self.assertIn("2 tâches", out.getvalue())
