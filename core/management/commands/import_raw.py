"""
import_raw.py

`manage.py import_raw <file.txt> [--project slug]`: one non-empty line
becomes one raw inbox task. See core/importer.py.
"""

import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.importer import import_raw_lines, parse_lines, read_lines
from core.models import Project
from core.services import get_project

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Import a raw to-do pile as inbox tasks."""

    help = "Importe un fichier texte brut : chaque ligne non vide devient une tâche brute."

    def add_arguments(self, parser) -> None:
        parser.add_argument("file", type=Path)
        parser.add_argument("--project", help="Slug du projet (ex. drab)")
        parser.add_argument(
            "--dry-run", action="store_true", help="Affiche le résultat sans rien écrire"
        )

    def handle(self, *args, **options) -> None:
        path: Path = options["file"]
        if not path.is_file():
            raise CommandError(f"Fichier introuvable : {path}")
        try:
            project = get_project(options["project"])
        except Project.DoesNotExist as exc:
            raise CommandError(f"Projet inconnu : {options['project']}") from exc
        try:
            lines = read_lines(path)
        except UnicodeDecodeError as exc:
            raise CommandError(f"Encodage du fichier non reconnu : {exc}") from exc

        if options["dry_run"]:
            parsed = parse_lines(lines)
            for item in parsed.lines:
                parent = (
                    f" (parent: ligne {parsed.lines[item.parent].line_no})"
                    if (item.parent is not None)
                    else ""
                )
                self.stdout.write(f"{item.line_no:>4} niv.{item.level} {item.title}{parent}")
            self.stdout.write(f"{len(parsed.lines)} tâches seraient créées.")
            return

        tasks = import_raw_lines(lines, project)
        self.stdout.write(self.style.SUCCESS(f"{len(tasks)} tâches créées (inbox, brut)."))
