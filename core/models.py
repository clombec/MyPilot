"""
models.py

Data models for Pilotage.

Layer 1 (facts): Project, Task, Event, DayLog, WorkSession, Note.
Layer 2 (soft memory): Observation, ProfileVersion, Rule, BriefingSpecVersion.
Cross-cutting: Proposal, Briefing, ConversationTurn, AuditLog.

Models only store data. No prioritisation or business rule lives here:
all planning is done by the agent (see CLAUDE_pilotage.md §2).
"""

from django.db import models


class Author(models.TextChoices):
    """Who created a record."""

    USER = "user", "utilisateur"
    AGENT = "agent", "agent"
    IMPORT = "import", "import"


class ReviewStatus(models.TextChoices):
    """Validation status for anything the agent proposes."""

    PROPOSED = "proposed", "proposé"
    ACCEPTED = "accepted", "accepté"
    REJECTED = "rejected", "refusé"


class Weekday(models.IntegerChoices):
    """ISO-like weekday, Monday = 0 (same as `date.weekday()`)."""

    MONDAY = 0, "lundi"
    TUESDAY = 1, "mardi"
    WEDNESDAY = 2, "mercredi"
    THURSDAY = 3, "jeudi"
    FRIDAY = 4, "vendredi"
    SATURDAY = 5, "samedi"
    SUNDAY = 6, "dimanche"


# --- Layer 1: facts ---------------------------------------------------------


class Project(models.Model):
    """One of the user's activities (DrAB, Aure Tech, ...)."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    priority = models.PositiveSmallIntegerField(help_text="1 = highest priority")
    description = models.TextField(blank=True)
    fixed_weekday = models.PositiveSmallIntegerField(
        choices=Weekday.choices, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self) -> str:
        return self.name


class Task(models.Model):
    """A task. Only `title` is required; the agent fills the rest over time."""

    class Status(models.TextChoices):
        INBOX = "inbox", "boîte de réception"
        TODO = "todo", "à faire"
        DOING = "doing", "en cours"
        WAITING = "waiting", "en attente"
        SOMEDAY = "someday", "un jour"
        DONE = "done", "fait"
        DROPPED = "dropped", "abandonné"

    class Clarity(models.TextChoices):
        RAW = "raw", "brut"
        PARTIAL = "partial", "partiel"
        CLEAR = "clear", "clair"

    title = models.CharField(max_length=500)
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.INBOX)
    raw_text = models.TextField(blank=True, help_text="Original text, never modified")
    notes = models.TextField(blank=True)
    est_minutes = models.PositiveIntegerField(null=True, blank=True)
    context = models.CharField(max_length=50, null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    not_before = models.DateField(null=True, blank=True)
    waiting_for = models.CharField(max_length=200, null=True, blank=True)
    waiting_since = models.DateField(null=True, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="subtasks"
    )
    linked_project = models.ForeignKey(
        Project,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="linked_tasks",
    )
    clarity = models.CharField(max_length=7, choices=Clarity.choices, default=Clarity.RAW)
    recurrence = models.CharField(max_length=200, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    done_at = models.DateTimeField(null=True, blank=True)
    created_by = models.CharField(max_length=6, choices=Author.choices, default=Author.USER)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["clarity"]),
            models.Index(fields=["due_date"]),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.title}"


class Event(models.Model):
    """A dated event (trip, training...) with a free-form checklist."""

    class Status(models.TextChoices):
        PLANNED = "planned", "prévu"
        CONFIRMED = "confirmed", "confirmé"
        DONE = "done", "passé"
        CANCELLED = "cancelled", "annulé"

    title = models.CharField(max_length=300)
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PLANNED)
    checklist = models.JSONField(
        default=list,
        blank=True,
        help_text='List of {"label": str, "done": bool}',
    )
    tasks = models.ManyToManyField(Task, blank=True, related_name="events")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "id"]

    def __str__(self) -> str:
        return f"{self.start_date} {self.title}"


class DayLog(models.Model):
    """What the user declared about one day. Nothing is assumed in advance."""

    date = models.DateField(unique=True)
    declared_focus = models.CharField(max_length=300, blank=True)
    planned_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    location = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    outcome = models.TextField(blank=True)
    agent_session_id = models.CharField(
        max_length=100, blank=True, help_text="Claude Code session resumed during the day"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self) -> str:
        return str(self.date)


class WorkSession(models.Model):
    """A block of work actually done (optional in phase 1)."""

    task = models.ForeignKey(
        Task, null=True, blank=True, on_delete=models.SET_NULL, related_name="sessions"
    )
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="sessions"
    )
    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start"]


class Note(models.Model):
    """Reference note (billing rule, holiday dates, contacts): not a task."""

    title = models.CharField(max_length=300)
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="ref_notes"
    )
    text = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True)
    created_by = models.CharField(max_length=6, choices=Author.choices, default=Author.USER)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


# --- Layer 2: soft memory ---------------------------------------------------


class Observation(models.Model):
    """Short free-text fact about the user's life. Append-only."""

    date = models.DateField()
    text = models.CharField(max_length=1000)
    tags = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=6, choices=Author.choices, default=Author.USER)
    pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"{self.date} {self.text[:50]}"


class ProfileVersion(models.Model):
    """One version of `profil.md`, maintained by the agent, validated by the user."""

    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=8, choices=ReviewStatus.choices, default=ReviewStatus.PROPOSED
    )
    rationale = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Rule(models.Model):
    """A standing instruction ('consigne') read and evaluated by the agent.

    There is no rule engine in the code: the agent decides when it applies.
    """

    text = models.CharField(max_length=500)
    trigger = models.CharField(max_length=300, blank=True)
    message = models.CharField(max_length=500, blank=True)
    active = models.BooleanField(default=False)
    status = models.CharField(
        max_length=8, choices=ReviewStatus.choices, default=ReviewStatus.PROPOSED
    )
    source_observation_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.text


class BriefingSpecVersion(models.Model):
    """One version of `briefing.md`: the wanted format of the morning briefing."""

    class Source(models.TextChoices):
        INITIAL = "initial", "version initiale"
        USER_REQUEST = "user_request", "demande de l'utilisateur"
        AGENT_PROPOSAL = "agent_proposal", "proposition de l'agent"

    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=15, choices=Source.choices)
    status = models.CharField(
        max_length=8, choices=ReviewStatus.choices, default=ReviewStatus.PROPOSED
    )
    instruction = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


# --- Cross-cutting ----------------------------------------------------------


class Proposal(models.Model):
    """A change suggested by the agent that waits for the user's decision."""

    class Kind(models.TextChoices):
        PROFILE = "profile", "profil"
        RULE = "rule", "consigne"
        BRIEFING = "briefing", "format du briefing"
        TASK_DROP = "task_drop", "abandon de tâche"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(
        max_length=8, choices=ReviewStatus.choices, default=ReviewStatus.PROPOSED
    )
    rationale = models.TextField(blank=True)
    profile_version = models.ForeignKey(
        ProfileVersion, null=True, blank=True, on_delete=models.CASCADE
    )
    rule = models.ForeignKey(Rule, null=True, blank=True, on_delete=models.CASCADE)
    briefing_spec = models.ForeignKey(
        BriefingSpecVersion, null=True, blank=True, on_delete=models.CASCADE
    )
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.CASCADE)
    discord_message_id = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} ({self.get_status_display()})"


class Briefing(models.Model):
    """A morning briefing as written by the agent."""

    date = models.DateField()
    content = models.TextField()
    sent_at = models.DateTimeField(null=True, blank=True)
    channel_message_id = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]


class ConversationTurn(models.Model):
    """One message exchanged with the agent."""

    class Role(models.TextChoices):
        USER = "user", "utilisateur"
        AGENT = "agent", "agent"

    role = models.CharField(max_length=5, choices=Role.choices)
    content = models.TextField()
    channel = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class AuditLog(models.Model):
    """Every write made by the agent, with enough data to undo it."""

    tool = models.CharField(max_length=100)
    arguments = models.JSONField(default=dict, blank=True)
    result = models.TextField(blank=True)
    undo_data = models.JSONField(
        default=dict, blank=True, help_text="State needed to revert the write"
    )
    undone = models.BooleanField(default=False)
    actor = models.CharField(max_length=6, choices=Author.choices, default=Author.AGENT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.tool}"
