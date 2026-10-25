"""
undo.py

Undo of the agent's writes, based on AuditLog.undo_data (command `/annuler`).

Undo data format: {"ops": [op, ...]}, applied in order. Supported ops:
- {"op": "delete", "model": "core.Task", "ids": [1, 2]}
- {"op": "set_fields", "model": "core.Task", "id": 1, "fields": {...},
   "m2m": {"tasks": [3, 4]}}   (m2m optional)
"""

import logging

from django.apps import apps
from django.db import transaction

from .models import AuditLog, Author

logger = logging.getLogger(__name__)


class NothingToUndo(Exception):
    """No agent write is left to undo."""


def _apply(op: dict) -> None:
    """Apply one undo operation."""
    model = apps.get_model(op["model"])
    if op["op"] == "delete":
        model.objects.filter(pk__in=op["ids"]).delete()
    elif op["op"] == "set_fields":
        obj = model.objects.filter(pk=op["id"]).first()
        if obj is None:
            logger.warning("Undo target %s #%s no longer exists", op["model"], op["id"])
            return
        for attname, value in op.get("fields", {}).items():
            field = next(f for f in model._meta.concrete_fields if f.attname == attname)
            setattr(obj, attname, field.to_python(value) if value is not None else None)
        obj.save()
        for name, ids in op.get("m2m", {}).items():
            getattr(obj, name).set(ids)
    else:
        raise ValueError(f"Unknown undo op: {op['op']}")


@transaction.atomic
def undo_entry(entry: AuditLog) -> AuditLog:
    """Revert one audit entry and mark it as undone.

    Args:
        entry: An agent AuditLog entry with undo data, not yet undone.

    Returns:
        The updated entry.

    Raises:
        ValueError: If the entry cannot be undone.
    """
    if entry.undone or not entry.undo_data.get("ops"):
        raise ValueError(f"Audit entry {entry.pk} cannot be undone")
    for op in entry.undo_data["ops"]:
        _apply(op)
    entry.undone = True
    entry.save(update_fields=["undone"])
    AuditLog.objects.create(
        tool="undo",
        arguments={"audit_id": entry.pk, "tool": entry.tool},
        result=f"Undid {entry.tool}",
        actor=Author.USER,
    )
    logger.info("Undid audit entry %s (%s)", entry.pk, entry.tool)
    return entry


def undo_last() -> AuditLog:
    """Undo the most recent agent write that is not undone yet.

    Returns:
        The undone AuditLog entry (its `result` describes what was reverted).

    Raises:
        NothingToUndo: If there is no agent write left to undo.
    """
    entry = (
        AuditLog.objects.filter(actor=Author.AGENT, undone=False)
        .exclude(undo_data={})
        .order_by("-created_at", "-id")
        .first()
    )
    if entry is None:
        raise NothingToUndo
    return undo_entry(entry)
