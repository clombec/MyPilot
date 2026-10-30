"""
agent_tools.py

Implementation of the tools exposed to the agent (CLAUDE_clabpilot.md §5).
`agent/mcp_server.py` only wraps these functions; they are plain Python so
they can be tested without Claude.

Every write:
- validates its arguments (ToolError with a short reason otherwise),
- is recorded in AuditLog with the data needed to undo it (see core/undo.py),
- returns a short text that the agent can repeat to the user.

Nothing here decides what matters: no scoring, no urgency rule.
"""

import logging
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from functools import wraps
from typing import Any

from django.db import transaction
from django.utils import timezone

from . import services
from .models import (
    AuditLog,
    Author,
    BriefingSpecVersion,
    DayLog,
    Event,
    Note,
    Observation,
    ProfileVersion,
    Project,
    Proposal,
    ReviewStatus,
    Rule,
    Task,
)
from .snapshot import build_snapshot

logger = logging.getLogger(__name__)

MAX_LIST = 50
DEFAULT_LIST = 20
TASK_EDITABLE_FIELDS = {
    "title",
    "project",
    "status",
    "notes",
    "est_minutes",
    "context",
    "due_date",
    "not_before",
    "waiting_for",
    "waiting_since",
    "parent_id",
    "linked_project",
    "clarity",
    "recurrence",
}
TASK_DATE_FIELDS = {"due_date", "not_before", "waiting_since"}
TASK_PROJECT_FIELDS = {"project", "linked_project"}
EVENT_EDITABLE_FIELDS = {
    "title",
    "project",
    "start_date",
    "end_date",
    "location",
    "notes",
    "status",
    "checklist",
    "task_ids",
}
# Obvious secrets: "mot de passe : xxx", "mdp=xxx", "password: xxx", API keys.
SECRET_RE = re.compile(
    r"(?:mot\s+de\s+passe|mdp|password|passwd|pwd|code\s+pin|pin)\s*[:=]\s*\S+"
    r"|\b(?:sk|pk|ghp|xox[bp])[-_][A-Za-z0-9_-]{16,}",
    re.IGNORECASE,
)


class ToolError(Exception):
    """Invalid tool call. The message is returned to the agent as is."""


# --- Helpers ----------------------------------------------------------------


def _check_secrets(*texts: str | None) -> None:
    """Refuse texts that obviously contain a password or a key."""
    for text in texts:
        if text and SECRET_RE.search(text):
            raise ToolError(
                "Refused: this text looks like it contains a password or a key. "
                "Never store secrets; suggest a password manager instead."
            )


def _parse_date(value: Any, field: str) -> date | None:
    """Parse an ISO date (YYYY-MM-DD). Empty values clear the field."""
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ToolError(f"{field}: expected a date YYYY-MM-DD, got {value!r}") from exc


def _project(slug: Any) -> Project | None:
    """Resolve a project slug. Empty values mean 'no project'."""
    if slug in (None, ""):
        return None
    try:
        return services.get_project(str(slug))
    except Project.DoesNotExist as exc:
        slugs = ", ".join(Project.objects.values_list("slug", flat=True))
        raise ToolError(f"Unknown project {slug!r}. Known: {slugs}") from exc


def _choice(value: Any, choices: type, field: str, allow_none: bool = True) -> str | None:
    """Validate a value against a TextChoices enum."""
    if value in (None, "") and allow_none:
        return None
    if value not in choices.values:
        raise ToolError(f"{field}: must be one of {', '.join(choices.values)}")
    return value


def _get(model: type, pk: Any, label: str):
    """Fetch an object by primary key or raise a ToolError."""
    try:
        return model.objects.get(pk=int(pk))
    except (model.DoesNotExist, TypeError, ValueError) as exc:
        raise ToolError(f"{label} #{pk} not found") from exc


def _snapshot_fields(obj, names: list[str]) -> dict[str, Any]:
    """Serialize the current value of some fields (for undo)."""
    data = {}
    for name in names:
        field = obj._meta.get_field(name)
        value = getattr(obj, field.attname)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        data[field.attname] = value
    return data


def _label(model: type) -> str:
    """Return the 'app.Model' label used in undo data."""
    return model._meta.label


def _task_line(task: Task) -> str:
    """One-line description of a task."""
    bits = [f"#{task.pk} {task.title}", f"[{task.status}/{task.clarity}]"]
    if task.project_id:
        bits.append(task.project.slug)
    if task.parent_id:
        bits.append(f"sous #{task.parent_id}")
    if task.due_date:
        bits.append(f"échéance {task.due_date.isoformat()}")
    if task.est_minutes:
        bits.append(f"{task.est_minutes} min")
    if task.context:
        bits.append(f"ctx {task.context}")
    if task.waiting_for:
        bits.append(f"attend {task.waiting_for}")
    return " · ".join(bits)


def audited(tool: str) -> Callable:
    """Decorator for write tools: run in a transaction and record an AuditLog.

    The wrapped function returns `(result_text, undo_data)`; the decorator
    returns only the text.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> str:
            with transaction.atomic():
                result, undo = func(*args, **kwargs)
                AuditLog.objects.create(
                    tool=tool,
                    arguments=_jsonable({"args": list(args), **kwargs}),
                    result=result[:1000],
                    undo_data=undo,
                    actor=Author.AGENT,
                )
            logger.info("Agent tool %s done", tool)
            return result

        return wrapper

    return decorator


def _jsonable(value: Any) -> Any:
    """Make tool arguments JSON-serializable for the audit log."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


# --- Read tools -------------------------------------------------------------


def get_snapshot(scope: str = "full") -> str:
    """Condensed view of the current situation (Markdown)."""
    if scope not in {"full", "light"}:
        raise ToolError("scope: must be 'full' or 'light'")
    return build_snapshot(scope=scope)


def list_projects() -> str:
    """List the projects with their slug and priority."""
    lines = []
    for p in Project.objects.all():
        day = f", jour fixe {p.get_fixed_weekday_display()}" if p.fixed_weekday is not None else ""
        lines.append(f"- {p.slug} : {p.name} (priorité {p.priority}{day})")
    return "\n".join(lines)


def list_tasks(
    project: str | None = None,
    status: str | None = None,
    clarity: str | None = None,
    due_before: str | None = None,
    query: str | None = None,
    parent_id: int | None = None,
    limit: int = DEFAULT_LIST,
) -> str:
    """List tasks with simple filters, ordered by due date then creation."""
    qs = Task.objects.select_related("project")
    if project == "none":
        qs = qs.filter(project__isnull=True)
    elif project:
        qs = qs.filter(project=_project(project))
    if status:
        qs = qs.filter(status__in=[s.strip() for s in status.split(",")])
    else:
        qs = qs.filter(status__in=services.OPEN_STATUSES)
    if clarity:
        qs = qs.filter(clarity__in=[c.strip() for c in clarity.split(",")])
    if due_before:
        qs = qs.filter(due_date__lte=_parse_date(due_before, "due_before"))
    if query:
        qs = qs.filter(title__icontains=query)
    if parent_id is not None:
        qs = qs.filter(parent_id=parent_id)
    limit = max(1, min(int(limit), MAX_LIST))
    total = qs.count()
    tasks = qs.order_by("due_date", "created_at", "id")[:limit]
    lines = [_task_line(t) for t in tasks]
    header = f"{total} tâche(s)" + (f", {limit} affichées" if total > limit else "")
    return "\n".join([header, *lines])


def get_task(task_id: int) -> str:
    """Full detail of one task, including its original raw text."""
    task = _get(Task, task_id, "Task")
    lines = [_task_line(task)]
    if task.raw_text and task.raw_text.strip() != task.title:
        lines.append(f"Texte d'origine : {task.raw_text.strip()}")
    if task.notes:
        lines.append(f"Notes : {task.notes}")
    if task.linked_project_id:
        lines.append(f"Projet lié : {task.linked_project.slug}")
    if task.not_before:
        lines.append(f"Pas avant : {task.not_before.isoformat()}")
    if task.waiting_since:
        lines.append(f"En attente depuis : {task.waiting_since.isoformat()}")
    if task.recurrence:
        lines.append(f"Récurrence : {task.recurrence}")
    subtasks = list(task.subtasks.all())
    if subtasks:
        lines.append("Sous-tâches :")
        lines += [f"  {_task_line(s)}" for s in subtasks]
    events = list(task.events.all())
    if events:
        lines.append("Événements : " + ", ".join(f"#{e.pk} {e.title}" for e in events))
    lines.append(f"Créée le {timezone.localtime(task.created_at):%Y-%m-%d} par {task.created_by}")
    return "\n".join(lines)


def list_events(start: str | None = None, end: str | None = None) -> str:
    """Events starting in a date range (default: from today on)."""
    qs = Event.objects.select_related("project")
    qs = qs.filter(start_date__gte=_parse_date(start, "start") or services.today())
    if end:
        qs = qs.filter(start_date__lte=_parse_date(end, "end"))
    lines = []
    for e in qs[:MAX_LIST]:
        items = ", ".join(f"{'☑' if i.get('done') else '☐'} {i.get('label')}" for i in e.checklist)
        span = f"{e.start_date.isoformat()}" + (
            f" → {e.end_date.isoformat()}" if e.end_date else ""
        )
        proj = f" [{e.project.slug}]" if e.project_id else ""
        lines.append(f"#{e.pk} {span} {e.title}{proj} ({e.status}) {items}".rstrip())
    return "\n".join(lines) or "Aucun événement."


def get_day(day: str | None = None) -> str:
    """What the user declared for a day (default: today)."""
    target = _parse_date(day, "date") or services.today()
    log = DayLog.objects.filter(date=target).first()
    if not log:
        return f"{target.isoformat()} : rien de déclaré."
    parts = [
        f"{target.isoformat()}",
        f"focus : {log.declared_focus or '?'}",
        f"heures : {log.planned_hours if log.planned_hours is not None else '?'}",
        f"lieu : {log.location or '?'}",
    ]
    if log.notes:
        parts.append(f"notes : {log.notes}")
    if log.outcome:
        parts.append(f"bilan : {log.outcome}")
    return " · ".join(parts)


def list_observations(limit: int = 20, tag: str | None = None) -> str:
    """Latest observations, optionally filtered by tag."""
    limit = max(1, min(int(limit), MAX_LIST))
    qs = Observation.objects.all()
    obs = [o for o in qs[: MAX_LIST * 4] if tag in o.tags][:limit] if tag else list(qs[:limit])
    lines = []
    for o in obs:
        tags = f" [{', '.join(o.tags)}]" if o.tags else ""
        pin = " 📌" if o.pinned else ""
        lines.append(f"#{o.pk} {o.date.isoformat()} {o.text}{tags}{pin}")
    return "\n".join(lines) or "Aucune observation."


def search_notes(query: str) -> str:
    """Search reference notes by title, text or tag."""
    query = (query or "").strip()
    if not query:
        raise ToolError("query: required")
    found = [
        n
        for n in Note.objects.select_related("project")
        if query.lower() in n.title.lower()
        or query.lower() in n.text.lower()
        or any(query.lower() == t.lower() for t in n.tags)
    ][:MAX_LIST]
    lines = []
    for n in found:
        proj = f" [{n.project.slug}]" if n.project_id else ""
        lines.append(f"#{n.pk} {n.title}{proj}\n{n.text[:600]}")
    return "\n\n".join(lines) or "Aucune note trouvée."


def get_briefing_spec() -> str:
    """Current briefing format (briefing.md) and its version id."""
    spec = services.get_active_briefing_spec()
    if not spec:
        return "Aucune spec de briefing active."
    return f"Version #{spec.pk} ({spec.created_at:%Y-%m-%d}) :\n{spec.content}"


# --- Free writes ------------------------------------------------------------


@audited("create_task")
def create_task(
    title: str,
    project: str | None = None,
    status: str = Task.Status.INBOX,
    clarity: str = Task.Clarity.RAW,
    notes: str = "",
    est_minutes: int | None = None,
    context: str | None = None,
    due_date: str | None = None,
    not_before: str | None = None,
    waiting_for: str | None = None,
    parent_id: int | None = None,
    linked_project: str | None = None,
) -> tuple[str, dict]:
    """Create a task. Only the title is required."""
    title = (title or "").strip()
    if not title:
        raise ToolError("title: required")
    _check_secrets(title, notes, context, waiting_for)
    if status in (Task.Status.DONE, Task.Status.DROPPED):
        raise ToolError("status: a new task cannot be done or dropped")
    parent = _valid_parent(parent_id, None)
    task = Task.objects.create(
        title=title[:500],
        raw_text=title,
        project=_project(project) or (parent.project if parent else None),
        status=_choice(status, Task.Status, "status", allow_none=False),
        clarity=_choice(clarity, Task.Clarity, "clarity", allow_none=False),
        notes=notes or "",
        est_minutes=est_minutes,
        context=context or None,
        due_date=_parse_date(due_date, "due_date"),
        not_before=_parse_date(not_before, "not_before"),
        waiting_for=waiting_for or None,
        waiting_since=services.today() if waiting_for else None,
        parent=parent,
        linked_project=_project(linked_project),
        created_by=Author.AGENT,
    )
    undo = {"ops": [{"op": "delete", "model": _label(Task), "ids": [task.pk]}]}
    return f"Tâche créée : {_task_line(task)}", undo


def _valid_parent(parent_id: Any, task: Task | None) -> Task | None:
    """Check the one-level rule: a parent is a top-level 'chantier'."""
    if parent_id in (None, ""):
        return None
    parent = _get(Task, parent_id, "Parent task")
    if parent.parent_id:
        raise ToolError(f"Task #{parent.pk} is itself a subtask: only one level allowed")
    if task is not None:
        if parent.pk == task.pk:
            raise ToolError("A task cannot be its own parent")
        if task.subtasks.exists():
            raise ToolError(f"Task #{task.pk} has subtasks: it cannot become a subtask")
    return parent


@audited("update_task")
def update_task(task_id: int, fields: dict[str, Any]) -> tuple[str, dict]:
    """Update some fields of a task. `raw_text` can never change."""
    task = _get(Task, task_id, "Task")
    if not fields:
        raise ToolError("fields: nothing to update")
    unknown = set(fields) - TASK_EDITABLE_FIELDS
    if unknown:
        raise ToolError(
            f"Not editable: {', '.join(sorted(unknown))}. "
            f"Editable: {', '.join(sorted(TASK_EDITABLE_FIELDS))}"
        )
    _check_secrets(*(str(v) for v in fields.values() if isinstance(v, str)))
    model_names = [
        "parent" if k == "parent_id" else k for k in fields if k in TASK_EDITABLE_FIELDS
    ]
    before = _snapshot_fields(task, [*model_names, "done_at", "waiting_since"])

    for key, value in fields.items():
        if key in TASK_PROJECT_FIELDS:
            setattr(task, key, _project(value))
        elif key in TASK_DATE_FIELDS:
            setattr(task, key, _parse_date(value, key))
        elif key == "status":
            if value == Task.Status.DROPPED:
                raise ToolError("Use propose_task_drop to drop a task (user approval needed)")
            task.status = _choice(value, Task.Status, "status", allow_none=False)
        elif key == "clarity":
            task.clarity = _choice(value, Task.Clarity, "clarity", allow_none=False)
        elif key == "parent_id":
            task.parent = _valid_parent(value, task)
        elif key == "title":
            if not str(value).strip():
                raise ToolError("title: cannot be empty")
            task.title = str(value).strip()[:500]
        elif key == "est_minutes":
            task.est_minutes = int(value) if value not in (None, "") else None
        else:  # notes, context, waiting_for, recurrence
            text = "" if value is None else str(value).strip()
            setattr(task, key, text if key == "notes" else (text or None))

    if task.status == Task.Status.DONE and not task.done_at:
        task.done_at = timezone.now()
    if task.status == Task.Status.WAITING and not task.waiting_since:
        task.waiting_since = services.today()
    task.save()
    undo = {"ops": [{"op": "set_fields", "model": _label(Task), "id": task.pk, "fields": before}]}
    return f"Tâche modifiée : {_task_line(task)}", undo


@audited("complete_task")
def complete_task(task_id: int) -> tuple[str, dict]:
    """Mark a task as done."""
    task = _get(Task, task_id, "Task")
    if task.status == Task.Status.DONE:
        raise ToolError(f"Task #{task.pk} is already done")
    before = _snapshot_fields(task, ["status", "done_at"])
    services.complete_task(task)
    undo = {"ops": [{"op": "set_fields", "model": _label(Task), "id": task.pk, "fields": before}]}
    return f"Tâche terminée : #{task.pk} {task.title}", undo


@audited("split_task")
def split_task(task_id: int, subtasks: list[str]) -> tuple[str, dict]:
    """Turn a task into a 'chantier' with the given subtasks."""
    task = _get(Task, task_id, "Task")
    if task.parent_id:
        raise ToolError(f"Task #{task.pk} is a subtask: only one level allowed")
    titles = [s.strip() for s in subtasks or [] if s and s.strip()]
    if not titles:
        raise ToolError("subtasks: give at least one title")
    _check_secrets(*titles)
    created = [
        Task.objects.create(
            title=t[:500],
            raw_text=t,
            project=task.project,
            parent=task,
            created_by=Author.AGENT,
        )
        for t in titles
    ]
    undo = {"ops": [{"op": "delete", "model": _label(Task), "ids": [t.pk for t in created]}]}
    listing = ", ".join(f"#{t.pk} {t.title}" for t in created)
    return f"Chantier #{task.pk} découpé en {len(created)} sous-tâches : {listing}", undo


def _checklist(items: Any) -> list[dict]:
    """Normalise a checklist: strings or {label, done} dicts."""
    result = []
    for item in items or []:
        if isinstance(item, str) and item.strip():
            result.append({"label": item.strip(), "done": False})
        elif isinstance(item, dict) and str(item.get("label", "")).strip():
            result.append({"label": str(item["label"]).strip(), "done": bool(item.get("done"))})
        else:
            raise ToolError(f"checklist: invalid item {item!r}")
    return result


@audited("create_event")
def create_event(
    title: str,
    start_date: str,
    end_date: str | None = None,
    project: str | None = None,
    location: str = "",
    notes: str = "",
    status: str = Event.Status.PLANNED,
    checklist: list | None = None,
    task_ids: list[int] | None = None,
) -> tuple[str, dict]:
    """Create a dated event with an optional checklist and linked tasks."""
    title = (title or "").strip()
    if not title:
        raise ToolError("title: required")
    _check_secrets(title, notes, location)
    start = _parse_date(start_date, "start_date")
    if start is None:
        raise ToolError("start_date: required")
    end = _parse_date(end_date, "end_date")
    if end and end < start:
        raise ToolError("end_date is before start_date")
    event = Event.objects.create(
        title=title[:300],
        start_date=start,
        end_date=end,
        project=_project(project),
        location=location or "",
        notes=notes or "",
        status=_choice(status, Event.Status, "status", allow_none=False),
        checklist=_checklist(checklist),
    )
    if task_ids:
        event.tasks.set(Task.objects.filter(pk__in=task_ids))
    undo = {"ops": [{"op": "delete", "model": _label(Event), "ids": [event.pk]}]}
    return f"Événement créé : #{event.pk} {start.isoformat()} {event.title}", undo


@audited("update_event")
def update_event(event_id: int, fields: dict[str, Any]) -> tuple[str, dict]:
    """Update an event. `checklist` replaces the whole list; `task_ids` the links."""
    event = _get(Event, event_id, "Event")
    unknown = set(fields or {}) - EVENT_EDITABLE_FIELDS
    if not fields or unknown:
        raise ToolError(f"Editable fields: {', '.join(sorted(EVENT_EDITABLE_FIELDS))}")
    _check_secrets(*(str(v) for v in fields.values() if isinstance(v, str)))
    names = [k for k in fields if k != "task_ids"]
    op = {
        "op": "set_fields",
        "model": _label(Event),
        "id": event.pk,
        "fields": _snapshot_fields(event, names),
    }
    if "task_ids" in fields:
        op["m2m"] = {"tasks": list(event.tasks.values_list("pk", flat=True))}
    for key, value in fields.items():
        if key == "project":
            event.project = _project(value)
        elif key in ("start_date", "end_date"):
            parsed = _parse_date(value, key)
            if key == "start_date" and parsed is None:
                raise ToolError("start_date cannot be empty")
            setattr(event, key, parsed)
        elif key == "status":
            event.status = _choice(value, Event.Status, "status", allow_none=False)
        elif key == "checklist":
            event.checklist = _checklist(value)
        elif key == "task_ids":
            continue
        else:
            setattr(event, key, value or "")
    event.save()
    if "task_ids" in fields:
        event.tasks.set(Task.objects.filter(pk__in=fields["task_ids"] or []))
    return f"Événement modifié : #{event.pk} {event.title}", {"ops": [op]}


@audited("create_note")
def create_note(
    title: str, text: str = "", project: str | None = None, tags: list[str] | None = None
) -> tuple[str, dict]:
    """Create a reference note (billing rule, contact, holiday dates...)."""
    title = (title or "").strip()
    if not title:
        raise ToolError("title: required")
    _check_secrets(title, text)
    note = Note.objects.create(
        title=title[:300],
        text=text or "",
        project=_project(project),
        tags=[t.strip() for t in tags or [] if t.strip()],
        created_by=Author.AGENT,
    )
    undo = {"ops": [{"op": "delete", "model": _label(Note), "ids": [note.pk]}]}
    return f"Note créée : #{note.pk} {note.title}", undo


@audited("set_day")
def set_day(
    day: str | None = None,
    focus: str | None = None,
    hours: float | None = None,
    location: str | None = None,
    notes: str | None = None,
) -> tuple[str, dict]:
    """Record what the user declared for a day. None leaves a field unchanged."""
    target = _parse_date(day, "date") or services.today()
    _check_secrets(focus, notes)
    log, created = DayLog.objects.get_or_create(date=target)
    names = ["declared_focus", "planned_hours", "location", "notes"]
    before = _snapshot_fields(log, names)
    if focus is not None:
        log.declared_focus = focus[:300]
    if hours is not None:
        if not 0 <= float(hours) <= 24:
            raise ToolError("hours: between 0 and 24")
        log.planned_hours = round(float(hours), 1)
    if location is not None:
        log.location = location[:100]
    if notes is not None:
        log.notes = notes
    log.save()
    if created:
        undo = {"ops": [{"op": "delete", "model": _label(DayLog), "ids": [log.pk]}]}
    else:
        undo = {
            "ops": [{"op": "set_fields", "model": _label(DayLog), "id": log.pk, "fields": before}]
        }
    return f"Journée enregistrée : {get_day(target.isoformat())}", undo


@audited("record_outcome")
def record_outcome(day: str | None, text: str) -> tuple[str, dict]:
    """Record the end-of-day outcome."""
    target = _parse_date(day, "date") or services.today()
    if not (text or "").strip():
        raise ToolError("text: required")
    _check_secrets(text)
    log, created = DayLog.objects.get_or_create(date=target)
    before = _snapshot_fields(log, ["outcome"])
    log.outcome = text.strip()
    log.save(update_fields=["outcome", "updated_at"])
    if created:
        undo = {"ops": [{"op": "delete", "model": _label(DayLog), "ids": [log.pk]}]}
    else:
        undo = {
            "ops": [{"op": "set_fields", "model": _label(DayLog), "id": log.pk, "fields": before}]
        }
    return f"Bilan du {target.isoformat()} enregistré.", undo


@audited("add_observation")
def add_observation(text: str, tags: list[str] | None = None) -> tuple[str, dict]:
    """Append an observation to the journal."""
    _check_secrets(text)
    try:
        obs = services.add_observation(text, tags, source=Author.AGENT)
    except ValueError as exc:
        raise ToolError("text: required") from exc
    undo = {"ops": [{"op": "delete", "model": _label(Observation), "ids": [obs.pk]}]}
    return f"Observation #{obs.pk} ajoutée : {obs.text}", undo


@audited("update_briefing_spec")
def update_briefing_spec(new_content: str, instruction: str) -> tuple[str, dict]:
    """Replace the briefing format. Only on an explicit request of the user."""
    if not (new_content or "").strip():
        raise ToolError("new_content: required")
    if not (instruction or "").strip():
        raise ToolError("instruction: quote the user's request")
    version = BriefingSpecVersion.objects.create(
        content=new_content.strip(),
        source=BriefingSpecVersion.Source.USER_REQUEST,
        status=ReviewStatus.ACCEPTED,
        instruction=instruction.strip(),
    )
    undo = {
        "ops": [
            {
                "op": "set_fields",
                "model": _label(BriefingSpecVersion),
                "id": version.pk,
                "fields": {"status": ReviewStatus.REJECTED},
            }
        ]
    }
    return f"Format du briefing mis à jour (version #{version.pk}).", undo


# --- Proposals (user approval required) -------------------------------------


def _proposal_undo(proposal: Proposal, target) -> dict:
    """Undo data for a proposal: delete the proposal, then its target."""
    ops = [{"op": "delete", "model": _label(Proposal), "ids": [proposal.pk]}]
    if target is not None:
        ops.append({"op": "delete", "model": _label(type(target)), "ids": [target.pk]})
    return {"ops": ops}


@audited("propose_briefing_change")
def propose_briefing_change(new_content: str, rationale: str) -> tuple[str, dict]:
    """Propose a briefing format change the user did not ask for."""
    if not (new_content or "").strip():
        raise ToolError("new_content: required")
    version = BriefingSpecVersion.objects.create(
        content=new_content.strip(),
        source=BriefingSpecVersion.Source.AGENT_PROPOSAL,
        status=ReviewStatus.PROPOSED,
        instruction=rationale or "",
    )
    proposal = Proposal.objects.create(
        kind=Proposal.Kind.BRIEFING, briefing_spec=version, rationale=rationale or ""
    )
    return (
        f"Proposition #{proposal.pk} (format du briefing) envoyée pour validation.",
        _proposal_undo(proposal, version),
    )


@audited("propose_profile_update")
def propose_profile_update(new_content: str, rationale: str) -> tuple[str, dict]:
    """Propose a new version of the profile."""
    if not (new_content or "").strip():
        raise ToolError("new_content: required")
    _check_secrets(new_content)
    version = ProfileVersion.objects.create(content=new_content.strip(), rationale=rationale or "")
    proposal = Proposal.objects.create(
        kind=Proposal.Kind.PROFILE, profile_version=version, rationale=rationale or ""
    )
    return (
        f"Proposition #{proposal.pk} (profil) envoyée pour validation.",
        _proposal_undo(proposal, version),
    )


@audited("propose_rule")
def propose_rule(
    text: str,
    trigger: str = "",
    message: str = "",
    source_observation_ids: list[int] | None = None,
) -> tuple[str, dict]:
    """Propose a standing rule ('consigne'), e.g. a reminder on office days."""
    if not (text or "").strip():
        raise ToolError("text: required")
    _check_secrets(text, message)
    ids = [int(i) for i in source_observation_ids or []]
    missing = set(ids) - set(Observation.objects.filter(pk__in=ids).values_list("pk", flat=True))
    if missing:
        raise ToolError(f"Unknown observation ids: {sorted(missing)}")
    rule = Rule.objects.create(
        text=text.strip()[:500],
        trigger=(trigger or "")[:300],
        message=(message or "")[:500],
        source_observation_ids=ids,
    )
    rationale = f"Observations : {', '.join(f'#{i}' for i in ids)}" if ids else ""
    proposal = Proposal.objects.create(kind=Proposal.Kind.RULE, rule=rule, rationale=rationale)
    return (
        f"Proposition #{proposal.pk} (consigne « {rule.text} ») envoyée pour validation.",
        _proposal_undo(proposal, rule),
    )


@audited("propose_task_drop")
def propose_task_drop(task_id: int, reason: str) -> tuple[str, dict]:
    """Propose to drop a task. Nothing is deleted without the user's approval."""
    task = _get(Task, task_id, "Task")
    if task.status in (Task.Status.DONE, Task.Status.DROPPED):
        raise ToolError(f"Task #{task.pk} is already {task.status}")
    proposal = Proposal.objects.create(
        kind=Proposal.Kind.TASK_DROP, task=task, rationale=reason or ""
    )
    return (
        f"Proposition #{proposal.pk} (abandon de #{task.pk} {task.title}) envoyée.",
        _proposal_undo(proposal, None),
    )


READ_TOOLS = [
    get_snapshot,
    list_projects,
    list_tasks,
    get_task,
    list_events,
    get_day,
    list_observations,
    search_notes,
    get_briefing_spec,
]
WRITE_TOOLS = [
    create_task,
    update_task,
    complete_task,
    split_task,
    create_event,
    update_event,
    create_note,
    set_day,
    record_outcome,
    add_observation,
    update_briefing_spec,
]
PROPOSAL_TOOLS = [
    propose_briefing_change,
    propose_profile_update,
    propose_rule,
    propose_task_drop,
]
