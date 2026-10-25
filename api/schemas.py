"""
schemas.py

Input and output schemas of the REST API (django-ninja / pydantic).
Projects are referenced by slug in inputs and outputs.
"""

import datetime as dt
from datetime import date, datetime
from decimal import Decimal

from ninja import ModelSchema, Schema

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


class ProjectOut(ModelSchema):
    class Meta:
        model = Project
        fields = "__all__"


class ProjectIn(Schema):
    slug: str
    name: str
    priority: int
    description: str = ""
    fixed_weekday: int | None = None
    notes: str = ""


class ProjectPatch(Schema):
    name: str | None = None
    priority: int | None = None
    description: str | None = None
    fixed_weekday: int | None = None
    notes: str | None = None


class TaskOut(ModelSchema):
    project_slug: str | None = None
    linked_project_slug: str | None = None

    class Meta:
        model = Task
        fields = "__all__"

    @staticmethod
    def resolve_project_slug(obj: Task) -> str | None:
        return obj.project.slug if obj.project else None

    @staticmethod
    def resolve_linked_project_slug(obj: Task) -> str | None:
        return obj.linked_project.slug if obj.linked_project else None


class TaskPatch(Schema):
    """Editable task fields. `raw_text` is deliberately absent: it never changes."""

    title: str | None = None
    project: str | None = None
    status: Task.Status | None = None
    notes: str | None = None
    est_minutes: int | None = None
    energy: str | None = None
    context: str | None = None
    due_date: date | None = None
    not_before: date | None = None
    waiting_for: str | None = None
    waiting_since: date | None = None
    parent_id: int | None = None
    linked_project: str | None = None
    clarity: Task.Clarity | None = None
    recurrence: str | None = None


class TaskIn(TaskPatch):
    title: str
    raw_text: str | None = None


class EventOut(ModelSchema):
    project_slug: str | None = None
    task_ids: list[int] = []

    class Meta:
        model = Event
        exclude = ["tasks"]

    @staticmethod
    def resolve_project_slug(obj: Event) -> str | None:
        return obj.project.slug if obj.project else None

    @staticmethod
    def resolve_task_ids(obj: Event) -> list[int]:
        return [t.pk for t in obj.tasks.all()]


class EventPatch(Schema):
    title: str | None = None
    project: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    notes: str | None = None
    status: Event.Status | None = None
    checklist: list[dict] | None = None
    task_ids: list[int] | None = None


class EventIn(EventPatch):
    title: str
    start_date: date


class NoteOut(ModelSchema):
    project_slug: str | None = None

    class Meta:
        model = Note
        fields = "__all__"

    @staticmethod
    def resolve_project_slug(obj: Note) -> str | None:
        return obj.project.slug if obj.project else None


class NotePatch(Schema):
    title: str | None = None
    project: str | None = None
    text: str | None = None
    tags: list[str] | None = None


class NoteIn(NotePatch):
    title: str


class ObservationOut(ModelSchema):
    class Meta:
        model = Observation
        fields = "__all__"


class ObservationIn(Schema):
    text: str
    tags: list[str] = []
    date: dt.date | None = None


class ObservationPin(Schema):
    pinned: bool


class RuleOut(ModelSchema):
    class Meta:
        model = Rule
        fields = "__all__"


class RulePatch(Schema):
    text: str | None = None
    trigger: str | None = None
    message: str | None = None
    active: bool | None = None


class RuleIn(Schema):
    text: str
    trigger: str = ""
    message: str = ""


class DayOut(ModelSchema):
    class Meta:
        model = DayLog
        exclude = ["agent_session_id"]


class DayIn(Schema):
    declared_focus: str | None = None
    planned_hours: Decimal | None = None
    energy: str | None = None
    location: str | None = None
    notes: str | None = None
    outcome: str | None = None


class BriefingOut(ModelSchema):
    class Meta:
        model = Briefing
        fields = "__all__"


class ProposalOut(ModelSchema):
    class Meta:
        model = Proposal
        fields = "__all__"


class SnapshotOut(Schema):
    date: dt.date
    markdown: str


class Message(Schema):
    detail: str


class Deleted(Schema):
    deleted: int
    at: datetime | None = None
