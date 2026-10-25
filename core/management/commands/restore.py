"""
restore.py

`manage.py restore <snapshot.json>`: replaces the current data with a
snapshot. A safety export of the current state is written first.
"""

import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.base import DeserializationError
from django.utils import timezone

from core.backup import restore_from_json, serialize_database

logger = logging.getLogger(__name__)

SAFETY_DIR = settings.BASE_DIR / "data" / "backups"


class Command(BaseCommand):
    """Restore the database from a JSON snapshot."""

    help = "Recharge la base depuis un snapshot JSON (remplace les données actuelles)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("file", type=Path)
        parser.add_argument(
            "--no-backup",
            action="store_true",
            help="Ne pas sauvegarder l'état actuel avant de restaurer",
        )

    def handle(self, *args, **options) -> None:
        path: Path = options["file"]
        if not path.is_file():
            raise CommandError(f"Fichier introuvable : {path}")

        if not options["no_backup"]:
            SAFETY_DIR.mkdir(parents=True, exist_ok=True)
            stamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
            safety = SAFETY_DIR / f"before_restore_{stamp}.json"
            safety.write_text(serialize_database(), encoding="utf-8")
            self.stdout.write(f"État actuel sauvegardé : {safety}")

        try:
            count = restore_from_json(path.read_text(encoding="utf-8"))
        except (ValueError, DeserializationError) as exc:
            logger.error("Restore failed: %s", exc)
            raise CommandError(f"Restauration impossible : {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"{count} objets restaurés."))
