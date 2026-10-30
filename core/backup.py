"""
backup.py

Nightly export and restore of the ClabPilot data.

`export_all` writes, in each export directory:
- `snapshot_YYYY-MM-DD.json`: full dump of the `core` models (used to restore),
- `memoire.md`: human- and Claude-readable summary of the memory.
Only the `core` app is exported: Django users (password hashes), sessions and
admin logs are never written to cloud folders.
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from django.apps import apps
from django.core import serializers
from django.db import transaction

from .models import Event, Observation, Task
from .services import OPEN_STATUSES, get_accepted_profile, get_active_rules, today

logger = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "snapshot_"
MEMORY_FILENAME = "memoire.md"
MEMORY_OBSERVATIONS = 50
MEMORY_EVENT_DAYS = 60

# Dependency order: referenced models first.
EXPORT_MODELS = [
    "core.Project",
    "core.Task",
    "core.Event",
    "core.DayLog",
    "core.WorkSession",
    "core.Note",
    "core.Observation",
    "core.ProfileVersion",
    "core.Rule",
    "core.BriefingSpecVersion",
    "core.Proposal",
    "core.Briefing",
    "core.ConversationTurn",
    "core.AuditLog",
]


def _export_models() -> list:
    """Return the model classes to export, in dependency order."""
    return [apps.get_model(label) for label in EXPORT_MODELS]


def serialize_database() -> str:
    """Serialize every exported model to a Django JSON fixture.

    Returns:
        The fixture as a JSON string, objects ordered by model then primary key.
    """
    objects = []
    for model in _export_models():
        objects.extend(model.objects.order_by("pk"))
    return serializers.serialize("json", objects, indent=1, ensure_ascii=False)


def build_memory_markdown(ref_date: date | None = None) -> str:
    """Build `memoire.md`: profile, rules, observations, open tasks, events.

    Args:
        ref_date: Reference date. Defaults to today.

    Returns:
        Markdown text.
    """
    ref_date = ref_date or today()
    out = [f"# Mémoire ClabPilot ({ref_date:%Y-%m-%d})", ""]

    profile = get_accepted_profile()
    out += ["## Profil", "", profile.content.strip() if profile else "_(aucun)_", ""]

    out += ["## Consignes actives", ""]
    rules = get_active_rules()
    out += [f"- {r.text} (quand : {r.trigger or '?'}) → {r.message}" for r in rules] or [
        "_(aucune)_"
    ]
    out.append("")

    out += [f"## {MEMORY_OBSERVATIONS} dernières observations", ""]
    for obs in Observation.objects.all()[:MEMORY_OBSERVATIONS]:
        tags = f" [{', '.join(obs.tags)}]" if obs.tags else ""
        out.append(f"- {obs.date:%Y-%m-%d} : {obs.text}{tags}")
    out.append("")

    out += ["## Tâches actives", ""]
    tasks = (
        Task.objects.filter(status__in=OPEN_STATUSES)
        .select_related("project")
        .order_by("project__priority", "status", "id")
    )
    current = object()
    for task in tasks:
        if task.project_id != current:
            current = task.project_id
            out += ["", f"### {task.project.name if task.project else 'Sans projet'}", ""]
        due = f", échéance {task.due_date:%Y-%m-%d}" if task.due_date else ""
        out.append(f"- #{task.pk} [{task.status}/{task.clarity}] {task.title}{due}")
    out.append("")

    out += ["## Événements à venir", ""]
    horizon = ref_date + timedelta(days=MEMORY_EVENT_DAYS)
    events = Event.objects.filter(start_date__gte=ref_date, start_date__lte=horizon).exclude(
        status=Event.Status.CANCELLED
    )
    for event in events:
        done = sum(1 for item in event.checklist if item.get("done"))
        out.append(
            f"- {event.start_date:%Y-%m-%d} {event.title} ({event.status}, "
            f"checklist {done}/{len(event.checklist)})"
        )
    if not events:
        out.append("_(aucun)_")
    out.append("")
    return "\n".join(out)


def rotate_snapshots(directory: Path, keep: int) -> list[Path]:
    """Delete the oldest JSON snapshots, keeping the `keep` most recent.

    Args:
        directory: Export directory.
        keep: Number of snapshots to keep.

    Returns:
        The deleted files.
    """
    files = sorted(directory.glob(f"{SNAPSHOT_PREFIX}*.json"))
    removed = files[:-keep] if keep > 0 else []
    for path in removed:
        path.unlink()
        logger.info("Old snapshot removed: %s", path.name)
    return removed


def export_all(
    directories: list[Path], keep: int = 30, ref_date: date | None = None
) -> list[Path]:
    """Write the JSON snapshot and `memoire.md` into each directory.

    Args:
        directories: Target directories (created if missing).
        keep: Number of JSON snapshots to keep per directory.
        ref_date: Date used in the file name. Defaults to today.

    Returns:
        Paths of the JSON snapshots written.

    Raises:
        OSError: If a directory cannot be written.
    """
    ref_date = ref_date or today()
    dump = serialize_database()
    memory = build_memory_markdown(ref_date)
    written = []
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        json_path = directory / f"{SNAPSHOT_PREFIX}{ref_date:%Y-%m-%d}.json"
        json_path.write_text(dump, encoding="utf-8")
        (directory / MEMORY_FILENAME).write_text(memory, encoding="utf-8")
        rotate_snapshots(directory, keep)
        written.append(json_path)
        logger.info("Snapshot exported to %s", directory)
    return written


@transaction.atomic
def restore_from_json(text: str) -> int:
    """Replace every exported model's content with the given fixture.

    All current `core` data is deleted first, inside a single transaction:
    if anything fails, the database is left untouched.

    Args:
        text: Fixture produced by `serialize_database`.

    Returns:
        Number of objects restored.

    Raises:
        ValueError: If the fixture contains a model outside the export list.
        django.core.serializers.base.DeserializationError: If the JSON is invalid.
    """
    allowed = {label.lower() for label in EXPORT_MODELS}
    labels = {entry.get("model", "") for entry in json.loads(text)}
    unknown = labels - allowed
    if unknown:
        raise ValueError(f"Unexpected models in snapshot: {sorted(unknown)}")

    for model in reversed(_export_models()):
        model.objects.all().delete()

    count = 0
    for obj in serializers.deserialize("json", text):
        obj.save()
        count += 1
    logger.info("Restore done: %d objects", count)
    return count
