"""
export_snapshot.py

`manage.py export_snapshot [--dir PATH ...]`: writes the JSON snapshot and
`memoire.md` into every export directory (EXPORT_DIRS by default).
"""

import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.backup import export_all

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_DIR = settings.BASE_DIR / "data" / "exports"


class Command(BaseCommand):
    """Export the database to the configured cloud folders."""

    help = "Exporte la base (JSON + memoire.md) dans les dossiers EXPORT_DIRS."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dir", action="append", type=Path, help="Dossier cible (remplace EXPORT_DIRS)"
        )
        parser.add_argument("--keep", type=int, default=settings.EXPORT_KEEP_JSON)

    def handle(self, *args, **options) -> None:
        directories = options["dir"] or settings.EXPORT_DIRS
        if not directories:
            logger.warning("EXPORT_DIRS is empty, using %s", DEFAULT_EXPORT_DIR)
            directories = [DEFAULT_EXPORT_DIR]
        try:
            written = export_all(directories, keep=options["keep"])
        except OSError as exc:
            logger.exception("Export failed")
            raise CommandError(f"Échec de l'export : {exc}") from exc
        for path in written:
            self.stdout.write(self.style.SUCCESS(f"Export écrit : {path}"))
