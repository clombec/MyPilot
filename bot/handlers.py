"""
handlers.py

Deterministic logic of the bot, independent from Discord: authorization,
commands that never call Claude (/note, /capture, /statut, /annuler...),
proposals and briefing format versions. All functions are synchronous
(Django ORM); the Discord layer calls them through `sync_to_async`.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from core import services
from core.models import (
    AuditLog,
    Author,
    Briefing,
    BriefingSpecVersion,
    ConversationTurn,
    DayLog,
    Project,
    Proposal,
    ReviewStatus,
    Task,
)
from core.undo import NothingToUndo, undo_last

from .config import BotConfig

logger = logging.getLogger(__name__)

PROPOSAL_PREVIEW = 1400
SPEC_HISTORY = 5
CAPTURE_PREFIX_RE = re.compile(r"^\s*#?([a-z][a-z0-9-]{1,30})\s*:\s*(.+)$", re.IGNORECASE | re.S)


def is_authorized(config: BotConfig, user_id: int, channel_id: int | None) -> bool:
    """Only the owner, only in the configured channels.

    Args:
        config: Bot configuration.
        user_id: Discord id of the author.
        channel_id: Discord id of the channel, or None (DM).

    Returns:
        True if the bot may process the event.
    """
    return user_id == config.owner_id and channel_id in config.channels


# --- Commands without Claude ------------------------------------------------


def note(text: str) -> str:
    """`/note`: append an observation."""
    try:
        obs = services.add_observation(text)
    except ValueError:
        return "Observation vide : rien n'a été enregistré."
    return f"📝 Observation #{obs.pk} enregistrée : {obs.text}"


def capture(text: str, project_slug: str | None = None) -> str:
    """`/capture` or a message in #capture: create a raw inbox task.

    A message like "clab: relancer la banque" goes to the `clab` project when
    the prefix is a known project slug.
    """
    if not project_slug:
        match = CAPTURE_PREFIX_RE.match(text or "")
        if match and Project.objects.filter(slug__iexact=match.group(1)).exists():
            project_slug, text = match.group(1), match.group(2)
    try:
        task = services.capture_task(text, project_slug)
    except ValueError:
        return "Capture vide : rien n'a été enregistré."
    except Project.DoesNotExist:
        return f"Projet inconnu : {project_slug}. Rien n'a été enregistré."
    project = f" [{task.project.slug}]" if task.project else ""
    return f"✅ #{task.pk}{project} {task.title}"


def status() -> str:
    """`/statut`: task counts and next due dates, without Claude."""
    counts = services.task_status_counts()
    raw = (
        Task.objects.filter(clarity=Task.Clarity.RAW)
        .exclude(status__in=[Task.Status.DONE, Task.Status.DROPPED])
        .count()
    )
    lines = [
        "**Statut**",
        f"📥 inbox {counts['inbox']} · à faire {counts['todo']} · en cours {counts['doing']} "
        f"· en attente {counts['waiting']} · un jour {counts['someday']}",
        f"🧩 non clarifiées : {raw} · ✅ faites : {counts['done']}",
    ]
    due = services.upcoming_due_tasks(days=14, limit=5)
    if due:
        lines.append("**Échéances**")
        lines += [
            f"• {t.due_date:%d/%m} #{t.pk} {t.title}"
            + (f" [{t.project.slug}]" if t.project else "")
            for t in due
        ]
    else:
        lines.append("Aucune échéance dans les 14 jours.")
    pending = Proposal.objects.filter(status=ReviewStatus.PROPOSED).count()
    if pending:
        lines.append(f"🗳️ {pending} proposition(s) en attente dans #propositions.")
    return "\n".join(lines)


def complete_by_id(task_id: int) -> str:
    """`/fait <id>`: close a task directly (no Claude)."""
    task = Task.objects.filter(pk=task_id).first()
    if task is None:
        return f"Tâche #{task_id} introuvable."
    if task.status == Task.Status.DONE:
        return f"La tâche #{task.pk} est déjà faite."
    before = {
        "status": task.status,
        "done_at": task.done_at.isoformat() if task.done_at else None,
    }
    services.complete_task(task)
    AuditLog.objects.create(
        tool="complete_task",
        arguments={"task_id": task.pk},
        result=f"Task {task.pk} done",
        undo_data={
            "ops": [{"op": "set_fields", "model": "core.Task", "id": task.pk, "fields": before}]
        },
        actor=Author.USER,
    )
    return f"✅ Fait : #{task.pk} {task.title}"


def undo() -> str:
    """`/annuler`: revert the last write of the agent."""
    try:
        entry = undo_last()
    except NothingToUndo:
        return "Rien à annuler : aucune écriture de l'agent en attente."
    when = timezone.localtime(entry.created_at)
    return f"↩️ Annulé : {entry.result} (outil {entry.tool}, {when:%d/%m %H:%M})."


def declare_day(focus: str, hours: float | None = None) -> str:
    """`/jour`: record the declared day (the agent then proposes a plan)."""
    log, _ = DayLog.objects.get_or_create(date=services.today())
    log.declared_focus = focus.strip()[:300]
    if hours is not None:
        log.planned_hours = round(hours, 1)
    log.save()
    bits = [log.declared_focus]
    if log.planned_hours is not None:
        bits.append(f"{log.planned_hours} h")
    return "Journée enregistrée : " + ", ".join(bits)


# --- Briefing ---------------------------------------------------------------


def briefing_sent_today() -> bool:
    """True if a briefing already exists for today."""
    return Briefing.objects.filter(date=services.today()).exists()


def mark_briefing_sent(briefing_id: int, message_id: int) -> None:
    """Store the Discord message id and send time of a briefing."""
    Briefing.objects.filter(pk=briefing_id).update(
        sent_at=timezone.now(), channel_message_id=str(message_id)
    )


def should_nudge() -> bool:
    """True if today's briefing got no answer from the user yet."""
    briefing = Briefing.objects.filter(date=services.today()).first()
    if briefing is None:
        return False
    since: datetime = briefing.sent_at or briefing.created_at
    answered = ConversationTurn.objects.filter(
        role=ConversationTurn.Role.USER, created_at__gte=since
    ).exists()
    return not answered


NUDGE_TEXT = (
    "👋 Petit rappel : qu'est-ce que tu fais aujourd'hui, combien de temps ? "
    "Réponds ici quand tu veux (ou `/jour`)."
)


@dataclass
class SpecOverview:
    """Current briefing spec and its recent versions."""

    text: str
    versions: list[tuple[int, str]]  # (id, label) for the restore menu


def briefing_format_overview() -> SpecOverview:
    """`/briefing-format`: current spec and the last versions."""
    current = services.get_active_briefing_spec()
    versions = list(BriefingSpecVersion.objects.filter(status=ReviewStatus.ACCEPTED)[:6])
    lines = [f"**Format actuel du briefing** (version #{current.pk if current else '?'})"]
    lines.append(f"```\n{(current.content if current else '(aucun)')[:1200]}\n```")
    older = [v for v in versions if not current or v.pk != current.pk][:SPEC_HISTORY]
    menu = []
    if older:
        lines.append("**Versions précédentes**")
        for v in older:
            reason = (v.instruction or v.get_source_display())[:60]
            label = f"#{v.pk} du {timezone.localtime(v.created_at):%d/%m %H:%M} — {reason}"
            lines.append(f"• {label}")
            menu.append((v.pk, label[:100]))
    return SpecOverview(text="\n".join(lines), versions=menu)


def restore_briefing_version(version_id: int) -> str:
    """Make an old spec version active again (as a new version, history kept)."""
    old = BriefingSpecVersion.objects.filter(pk=version_id).first()
    if old is None:
        return f"Version #{version_id} introuvable."
    new = BriefingSpecVersion.objects.create(
        content=old.content,
        source=BriefingSpecVersion.Source.USER_REQUEST,
        status=ReviewStatus.ACCEPTED,
        instruction=f"Retour à la version #{old.pk}",
    )
    return (
        f"↩️ Format du briefing restauré depuis la version #{old.pk} (nouvelle version #{new.pk})."
    )


# --- Proposals --------------------------------------------------------------


def pending_unposted_proposals() -> list[int]:
    """Ids of proposals waiting for a decision and not yet posted on Discord."""
    return list(
        Proposal.objects.filter(status=ReviewStatus.PROPOSED, discord_message_id="")
        .order_by("created_at", "id")
        .values_list("pk", flat=True)
    )


def proposal_text(proposal_id: int) -> str:
    """Human-readable text of a proposal (French)."""
    p = Proposal.objects.select_related("rule", "profile_version", "briefing_spec", "task").get(
        pk=proposal_id
    )
    why = f"\n_Pourquoi : {p.rationale}_" if p.rationale else ""
    if p.kind == Proposal.Kind.RULE and p.rule:
        body = (
            f"📌 **Consigne proposée** (#{p.pk})\n**{p.rule.text}**\n"
            f"Quand : {p.rule.trigger or '?'}\nMessage : {p.rule.message or '?'}"
        )
    elif p.kind == Proposal.Kind.PROFILE and p.profile_version:
        content = p.profile_version.content[:PROPOSAL_PREVIEW]
        body = f"👤 **Nouveau profil proposé** (#{p.pk})\n```\n{content}\n```"
        why = f"\n_Pourquoi : {p.profile_version.rationale or p.rationale}_"
    elif p.kind == Proposal.Kind.BRIEFING and p.briefing_spec:
        content = p.briefing_spec.content[:PROPOSAL_PREVIEW]
        body = f"🗞️ **Nouveau format de briefing proposé** (#{p.pk})\n```\n{content}\n```"
    elif p.kind == Proposal.Kind.TASK_DROP and p.task:
        body = f"🗑️ **Abandonner la tâche** #{p.task.pk} « {p.task.title} » ? (#{p.pk})"
    else:
        body = f"Proposition #{p.pk} ({p.get_kind_display()})"
    return (body + why)[:1900]


def mark_proposal_posted(proposal_id: int, message_id: int) -> None:
    """Remember the Discord message of a proposal."""
    Proposal.objects.filter(pk=proposal_id).update(discord_message_id=str(message_id))


@transaction.atomic
def decide(proposal_id: int, accept: bool) -> str:
    """Accept or refuse a proposal (Discord button)."""
    proposal = Proposal.objects.select_for_update().filter(pk=proposal_id).first()
    if proposal is None:
        return "Proposition introuvable (annulée ?)."
    try:
        services.decide_proposal(proposal, accept)
    except ValueError:
        return f"Proposition #{proposal.pk} déjà traitée ({proposal.get_status_display()})."
    verdict = "✅ Acceptée" if accept else "❌ Refusée"
    return f"{verdict} : proposition #{proposal.pk} ({proposal.get_kind_display()})."
