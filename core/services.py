"""
services.py

Plain data services shared by the web interface, the API, the bot and the
agent tools. These functions record and read data; they never decide what
matters (no scoring, no urgency rules). See CLAUDE_clabpilot.md §2.
"""

import logging
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from .models import (
    AuditLog,
    Author,
    BriefingSpecVersion,
    Observation,
    ProfileVersion,
    Project,
    Proposal,
    ReviewStatus,
    Rule,
    Task,
)

logger = logging.getLogger(__name__)

OPEN_STATUSES = (
    Task.Status.INBOX,
    Task.Status.TODO,
    Task.Status.DOING,
    Task.Status.WAITING,
    Task.Status.SOMEDAY,
)


def today() -> date:
    """Return today's date in the configured time zone."""
    return timezone.localdate()


def get_project(slug: str | None) -> Project | None:
    """Return the project with the given slug, or None if slug is empty.

    Args:
        slug: Project slug, case-insensitive. Empty or None means "no project".

    Returns:
        The matching Project, or None when no slug is given.

    Raises:
        Project.DoesNotExist: If a slug is given but matches no project.
    """
    if not slug:
        return None
    return Project.objects.get(slug__iexact=slug.strip())


def capture_task(
    text: str,
    project_slug: str | None = None,
    created_by: str = Author.USER,
) -> Task:
    """Create a raw inbox task from a free-text capture.

    Args:
        text: Text typed by the user. Stored verbatim in `raw_text`.
        project_slug: Optional project slug.
        created_by: Author of the capture.

    Returns:
        The created Task.

    Raises:
        ValueError: If the text is empty.
        Project.DoesNotExist: If the project slug is unknown.
    """
    title = text.strip()
    if not title:
        raise ValueError("Empty capture text")
    task = Task.objects.create(
        title=title[:500],
        raw_text=text,
        project=get_project(project_slug),
        created_by=created_by,
    )
    logger.info("Task captured: id=%s", task.pk)
    return task


def complete_task(task: Task) -> Task:
    """Mark a task as done and stamp the completion time.

    Args:
        task: The task to close.

    Returns:
        The updated Task.
    """
    task.status = Task.Status.DONE
    task.done_at = timezone.now()
    task.save(update_fields=["status", "done_at", "updated_at"])
    logger.info("Task completed: id=%s", task.pk)
    return task


def add_observation(
    text: str,
    tags: list[str] | None = None,
    source: str = Author.USER,
    obs_date: date | None = None,
) -> Observation:
    """Append an observation to the soft memory.

    Args:
        text: Short free text.
        tags: Optional free tags.
        source: Author of the observation.
        obs_date: Date of the observation. Defaults to today.

    Returns:
        The created Observation.

    Raises:
        ValueError: If the text is empty.
    """
    text = text.strip()
    if not text:
        raise ValueError("Empty observation text")
    obs = Observation.objects.create(
        date=obs_date or today(),
        text=text[:1000],
        tags=[t.strip() for t in (tags or []) if t.strip()],
        source=source,
    )
    logger.info("Observation added: id=%s", obs.pk)
    return obs


def get_accepted_profile() -> ProfileVersion | None:
    """Return the latest accepted profile version, if any."""
    return ProfileVersion.objects.filter(status=ReviewStatus.ACCEPTED).first()


def get_active_briefing_spec() -> BriefingSpecVersion | None:
    """Return the latest accepted briefing spec version, if any."""
    return BriefingSpecVersion.objects.filter(status=ReviewStatus.ACCEPTED).first()


def get_active_rules() -> list[Rule]:
    """Return accepted and active rules, newest first."""
    return list(Rule.objects.filter(status=ReviewStatus.ACCEPTED, active=True))


def task_status_counts() -> dict[str, int]:
    """Count tasks per status (used by `/statut`, no agent involved).

    Returns:
        Mapping of status value to count, including zero counts.
    """
    counts = {value: 0 for value, _ in Task.Status.choices}
    for row in Task.objects.values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    return counts


def upcoming_due_tasks(days: int = 14, limit: int = 20) -> list[Task]:
    """Return open tasks with a due date within the next `days` days.

    This is a plain date filter, sorted by due date. It does not judge urgency.

    Args:
        days: Window size in days, starting today (overdue tasks included).
        limit: Maximum number of tasks returned.

    Returns:
        List of tasks ordered by due date.
    """
    horizon = today() + timedelta(days=days)
    qs = Task.objects.filter(status__in=OPEN_STATUSES, due_date__lte=horizon)
    return list(qs.select_related("project").order_by("due_date", "id")[:limit])


@transaction.atomic
def decide_proposal(proposal: Proposal, accept: bool) -> Proposal:
    """Accept or reject a pending proposal and apply its effect.

    Accepting a profile or briefing proposal makes that version the active one
    (the latest accepted version wins). Accepting a rule activates it.
    Accepting a task drop sets the task status to `dropped` (never deleted).

    Args:
        proposal: A proposal in `proposed` status.
        accept: True to accept, False to reject.

    Returns:
        The updated Proposal.

    Raises:
        ValueError: If the proposal was already decided.
    """
    if proposal.status != ReviewStatus.PROPOSED:
        raise ValueError(f"Proposal {proposal.pk} already decided")
    new_status = ReviewStatus.ACCEPTED if accept else ReviewStatus.REJECTED

    if proposal.kind == Proposal.Kind.PROFILE and proposal.profile_version:
        proposal.profile_version.status = new_status
        proposal.profile_version.save(update_fields=["status"])
    elif proposal.kind == Proposal.Kind.RULE and proposal.rule:
        proposal.rule.status = new_status
        proposal.rule.active = accept
        proposal.rule.save(update_fields=["status", "active", "updated_at"])
    elif proposal.kind == Proposal.Kind.BRIEFING and proposal.briefing_spec:
        proposal.briefing_spec.status = new_status
        proposal.briefing_spec.save(update_fields=["status"])
    elif proposal.kind == Proposal.Kind.TASK_DROP and proposal.task and accept:
        proposal.task.status = Task.Status.DROPPED
        proposal.task.save(update_fields=["status", "updated_at"])

    proposal.status = new_status
    proposal.decided_at = timezone.now()
    proposal.save(update_fields=["status", "decided_at"])
    AuditLog.objects.create(
        tool="decide_proposal",
        arguments={"proposal_id": proposal.pk, "accept": accept},
        result=new_status,
        actor=Author.USER,
    )
    logger.info("Proposal %s %s", proposal.pk, new_status)
    return proposal
