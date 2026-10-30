"""
api.py

REST API (django-ninja), protected by a bearer token (`API_TOKEN`).
Plain CRUD: filters and sorts only, no prioritisation logic.
Meant to listen on the Tailscale interface only.
"""

import logging
import secrets
from datetime import date

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import NinjaAPI, Router
from ninja.errors import HttpError
from ninja.security import HttpBearer

from core import services
from core.models import (
    Author,
    Briefing,
    DayLog,
    Event,
    Note,
    Observation,
    Project,
    Proposal,
    ReviewStatus,
    Rule,
    Task,
)
from core.snapshot import build_snapshot

from . import schemas as s

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


class TokenAuth(HttpBearer):
    """Bearer token check against `settings.API_TOKEN`."""

    def authenticate(self, request: HttpRequest, token: str) -> str | None:
        expected = settings.API_TOKEN
        if not expected:
            logger.warning("API_TOKEN is not set: every API request is refused")
            return None
        if secrets.compare_digest(token, expected):
            return token
        return None


api = NinjaAPI(title="ClabPilot API", version="1", auth=TokenAuth(), urls_namespace="api")


def _project_or_400(slug: str | None) -> Project | None:
    """Resolve a project slug, or raise HTTP 400 if unknown."""
    try:
        return services.get_project(slug)
    except Project.DoesNotExist as exc:
        raise HttpError(400, f"Projet inconnu : {slug}") from exc


def _limit(value: int) -> int:
    """Clamp a list limit to a sane range."""
    return max(1, min(value, MAX_LIMIT))


def _apply(obj, data: dict, project_fields: tuple[str, ...] = ("project",)) -> None:
    """Copy validated fields onto a model instance, resolving project slugs."""
    for key, value in data.items():
        if key in project_fields:
            setattr(obj, key, _project_or_400(value))
        else:
            setattr(obj, key, value)


# --- Projects ---------------------------------------------------------------

projects = Router(tags=["projects"])


@projects.get("", response=list[s.ProjectOut])
def list_projects(request):
    return Project.objects.all()


@projects.get("/{slug}", response=s.ProjectOut)
def get_project(request, slug: str):
    return get_object_or_404(Project, slug=slug)


@projects.post("", response={201: s.ProjectOut})
def create_project(request, payload: s.ProjectIn):
    return 201, Project.objects.create(**payload.dict())


@projects.patch("/{slug}", response=s.ProjectOut)
def update_project(request, slug: str, payload: s.ProjectPatch):
    project = get_object_or_404(Project, slug=slug)
    _apply(project, payload.dict(exclude_unset=True), project_fields=())
    project.save()
    return project


@projects.delete("/{slug}", response=s.Deleted)
def delete_project(request, slug: str):
    deleted, _ = get_object_or_404(Project, slug=slug).delete()
    return {"deleted": deleted}


# --- Tasks ------------------------------------------------------------------

tasks = Router(tags=["tasks"])
TASK_PROJECT_FIELDS = ("project", "linked_project")


@tasks.get("", response=list[s.TaskOut])
def list_tasks(
    request,
    project: str | None = None,
    status: str | None = None,
    clarity: str | None = None,
    due_before: date | None = None,
    q: str | None = None,
    order: str = "created_at",
    limit: int = DEFAULT_LIMIT,
):
    qs = Task.objects.select_related("project", "linked_project")
    if project:
        qs = (
            qs.filter(project__isnull=True)
            if project == "none"
            else qs.filter(project__slug=project)
        )
    if status:
        qs = qs.filter(status__in=status.split(","))
    if clarity:
        qs = qs.filter(clarity__in=clarity.split(","))
    if due_before:
        qs = qs.filter(due_date__lte=due_before)
    if q:
        qs = qs.filter(title__icontains=q)
    if order.lstrip("-") not in {"created_at", "due_date", "updated_at", "id"}:
        raise HttpError(400, "Tri non autorisé")
    return qs.order_by(order, "id")[: _limit(limit)]


@tasks.get("/{task_id}", response=s.TaskOut)
def get_task(request, task_id: int):
    return get_object_or_404(Task, pk=task_id)


@tasks.post("", response={201: s.TaskOut})
def create_task(request, payload: s.TaskIn):
    data = payload.dict(exclude_unset=True)
    task = Task(raw_text=data.pop("raw_text", None) or payload.title, created_by=Author.USER)
    _apply(task, data, TASK_PROJECT_FIELDS)
    task.save()
    return 201, task


@tasks.patch("/{task_id}", response=s.TaskOut)
def update_task(request, task_id: int, payload: s.TaskPatch):
    task = get_object_or_404(Task, pk=task_id)
    data = payload.dict(exclude_unset=True)
    _apply(task, data, TASK_PROJECT_FIELDS)
    if data.get("status") == Task.Status.DONE and not task.done_at:
        task.done_at = timezone.now()
    task.save()
    return task


@tasks.post("/{task_id}/done", response=s.TaskOut)
def complete_task(request, task_id: int):
    return services.complete_task(get_object_or_404(Task, pk=task_id))


@tasks.delete("/{task_id}", response=s.Deleted)
def delete_task(request, task_id: int):
    deleted, _ = get_object_or_404(Task, pk=task_id).delete()
    return {"deleted": deleted}


# --- Events -----------------------------------------------------------------

events = Router(tags=["events"])


@events.get("", response=list[s.EventOut])
def list_events(request, start: date | None = None, end: date | None = None):
    qs = Event.objects.select_related("project").prefetch_related("tasks")
    if start:
        qs = qs.filter(start_date__gte=start)
    if end:
        qs = qs.filter(start_date__lte=end)
    return qs


@events.get("/{event_id}", response=s.EventOut)
def get_event(request, event_id: int):
    return get_object_or_404(Event, pk=event_id)


@events.post("", response={201: s.EventOut})
@transaction.atomic
def create_event(request, payload: s.EventIn):
    data = payload.dict(exclude_unset=True)
    task_ids = data.pop("task_ids", None)
    event = Event()
    _apply(event, data)
    event.save()
    if task_ids is not None:
        event.tasks.set(Task.objects.filter(pk__in=task_ids))
    return 201, event


@events.patch("/{event_id}", response=s.EventOut)
@transaction.atomic
def update_event(request, event_id: int, payload: s.EventPatch):
    event = get_object_or_404(Event, pk=event_id)
    data = payload.dict(exclude_unset=True)
    task_ids = data.pop("task_ids", None)
    _apply(event, data)
    event.save()
    if task_ids is not None:
        event.tasks.set(Task.objects.filter(pk__in=task_ids))
    return event


@events.delete("/{event_id}", response=s.Deleted)
def delete_event(request, event_id: int):
    deleted, _ = get_object_or_404(Event, pk=event_id).delete()
    return {"deleted": deleted}


# --- Notes ------------------------------------------------------------------

notes = Router(tags=["notes"])


@notes.get("", response=list[s.NoteOut])
def list_notes(request, q: str | None = None, project: str | None = None):
    qs = Note.objects.select_related("project")
    if project:
        qs = qs.filter(project__slug=project)
    if q:
        qs = qs.filter(title__icontains=q) | qs.filter(text__icontains=q)
    return qs


@notes.get("/{note_id}", response=s.NoteOut)
def get_note(request, note_id: int):
    return get_object_or_404(Note, pk=note_id)


@notes.post("", response={201: s.NoteOut})
def create_note(request, payload: s.NoteIn):
    note = Note(created_by=Author.USER)
    _apply(note, payload.dict(exclude_unset=True))
    note.save()
    return 201, note


@notes.patch("/{note_id}", response=s.NoteOut)
def update_note(request, note_id: int, payload: s.NotePatch):
    note = get_object_or_404(Note, pk=note_id)
    _apply(note, payload.dict(exclude_unset=True))
    note.save()
    return note


@notes.delete("/{note_id}", response=s.Deleted)
def delete_note(request, note_id: int):
    deleted, _ = get_object_or_404(Note, pk=note_id).delete()
    return {"deleted": deleted}


# --- Observations (append-only, only `pinned` can change) -------------------

observations = Router(tags=["observations"])


@observations.get("", response=list[s.ObservationOut])
def list_observations(request, limit: int = 50, tag: str | None = None):
    qs = Observation.objects.all()
    if tag:
        # JSON containment is not available on SQLite: filter in Python.
        return [o for o in qs[:MAX_LIMIT] if tag in o.tags][: _limit(limit)]
    return qs[: _limit(limit)]


@observations.post("", response={201: s.ObservationOut})
def create_observation(request, payload: s.ObservationIn):
    try:
        obs = services.add_observation(payload.text, payload.tags, obs_date=payload.date)
    except ValueError as exc:
        raise HttpError(400, "Texte vide") from exc
    return 201, obs


@observations.patch("/{obs_id}", response=s.ObservationOut)
def pin_observation(request, obs_id: int, payload: s.ObservationPin):
    obs = get_object_or_404(Observation, pk=obs_id)
    obs.pinned = payload.pinned
    obs.save(update_fields=["pinned"])
    return obs


# --- Rules ------------------------------------------------------------------

rules = Router(tags=["rules"])


@rules.get("", response=list[s.RuleOut])
def list_rules(request, status: str | None = None, active: bool | None = None):
    qs = Rule.objects.all()
    if status:
        qs = qs.filter(status=status)
    if active is not None:
        qs = qs.filter(active=active)
    return qs


@rules.post("", response={201: s.RuleOut})
def create_rule(request, payload: s.RuleIn):
    # A rule written by the user is accepted at once; only agent rules need review.
    rule = Rule.objects.create(**payload.dict(), status=ReviewStatus.ACCEPTED, active=True)
    return 201, rule


@rules.patch("/{rule_id}", response=s.RuleOut)
def update_rule(request, rule_id: int, payload: s.RulePatch):
    rule = get_object_or_404(Rule, pk=rule_id)
    _apply(rule, payload.dict(exclude_unset=True), project_fields=())
    rule.save()
    return rule


@rules.delete("/{rule_id}", response=s.Deleted)
def delete_rule(request, rule_id: int):
    deleted, _ = get_object_or_404(Rule, pk=rule_id).delete()
    return {"deleted": deleted}


# --- Days -------------------------------------------------------------------

days = Router(tags=["days"])


@days.get("", response=list[s.DayOut])
def list_days(request, start: date | None = None, end: date | None = None):
    qs = DayLog.objects.all()
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)
    return qs[:MAX_LIMIT]


@days.get("/{day}", response=s.DayOut)
def get_day(request, day: date):
    return get_object_or_404(DayLog, date=day)


@days.put("/{day}", response=s.DayOut)
def set_day(request, day: date, payload: s.DayIn):
    log, _ = DayLog.objects.get_or_create(date=day)
    _apply(log, payload.dict(exclude_unset=True), project_fields=())
    log.save()
    return log


# --- Snapshot, briefings, proposals -----------------------------------------

misc = Router(tags=["agent"])


@misc.get("/snapshot", response=s.SnapshotOut)
def snapshot(request, scope: str = "full"):
    ref = services.today()
    return {"date": ref, "markdown": build_snapshot(ref, scope=scope)}


@misc.get("/briefings", response=list[s.BriefingOut])
def list_briefings(request, limit: int = 30):
    return Briefing.objects.all()[: _limit(limit)]


@misc.get("/briefings/{briefing_id}", response=s.BriefingOut)
def get_briefing(request, briefing_id: int):
    return get_object_or_404(Briefing, pk=briefing_id)


@misc.get("/proposals", response=list[s.ProposalOut])
def list_proposals(request, status: str = ReviewStatus.PROPOSED):
    return Proposal.objects.filter(status=status)


@misc.post("/proposals/{proposal_id}/{decision}", response=s.ProposalOut)
def decide_proposal(request, proposal_id: int, decision: str):
    if decision not in {"accept", "reject"}:
        raise HttpError(400, "Décision inconnue (accept ou reject)")
    proposal = get_object_or_404(Proposal, pk=proposal_id)
    try:
        return services.decide_proposal(proposal, accept=decision == "accept")
    except ValueError as exc:
        raise HttpError(409, "Proposition déjà traitée") from exc


api.add_router("/projects", projects)
api.add_router("/tasks", tasks)
api.add_router("/events", events)
api.add_router("/notes", notes)
api.add_router("/observations", observations)
api.add_router("/rules", rules)
api.add_router("/days", days)
api.add_router("", misc)
