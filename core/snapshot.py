"""
snapshot.py

Compact Markdown summary of the current state, given to the agent
(CLAUDE_clabpilot.md §7). It only summarises: no decision, no scoring.
Lists are cut with plain limits and ordered by date or creation.
"""

from datetime import date, timedelta

from django.db.models import F, Q

from .models import DayLog, Event, Observation, Project, Task
from .services import get_accepted_profile, get_active_rules, task_status_counts, today

WEEKDAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
RECENT_DAYS = 7
RECENT_OBSERVATIONS = 15
HORIZON_DAYS = 14
WAITING_DAYS = 5
TODO_PER_PROJECT = 5
MAX_LIST = 25
UNCLEAR_SAMPLE = 10


def _fmt_task(task: Task) -> str:
    """One-line description of a task."""
    parts = [f"#{task.pk} {task.title}"]
    if task.project:
        parts.append(f"[{task.project.slug}]")
    if task.due_date:
        parts.append(f"échéance {task.due_date:%d/%m}")
    if task.est_minutes:
        parts.append(f"{task.est_minutes} min")
    if task.waiting_for:
        parts.append(f"attend {task.waiting_for}")
    return "- " + " · ".join(parts)


def _fmt_day(day: DayLog) -> str:
    """One-line description of a declared day."""
    bits = [f"{WEEKDAYS_FR[day.date.weekday()]} {day.date:%d/%m}"]
    if day.declared_focus:
        bits.append(f"focus {day.declared_focus}")
    if day.planned_hours is not None:
        bits.append(f"{day.planned_hours} h")
    if day.location:
        bits.append(f"lieu {day.location}")
    if day.outcome:
        bits.append(f"bilan : {day.outcome[:120]}")
    return "- " + " · ".join(bits)


def build_snapshot(ref_date: date | None = None, scope: str = "full") -> str:
    """Build the Markdown snapshot read by the agent.

    Args:
        ref_date: Reference date. Defaults to today.
        scope: 'full' for everything, 'light' to skip profile and rules
            (useful within a resumed session that already has them).

    Returns:
        Markdown text, kept short on purpose.
    """
    ref_date = ref_date or today()
    horizon = ref_date + timedelta(days=HORIZON_DAYS)
    out = [f"# Situation au {WEEKDAYS_FR[ref_date.weekday()]} {ref_date:%d/%m/%Y}", ""]

    # 1. Days
    day = DayLog.objects.filter(date=ref_date).first()
    declared = day and (day.declared_focus or day.planned_hours is not None or day.location)
    out += ["## Journée", "", _fmt_day(day) if declared else "- (pas encore déclarée)", ""]
    recent = DayLog.objects.filter(
        date__lt=ref_date, date__gte=ref_date - timedelta(days=RECENT_DAYS)
    ).exclude(declared_focus="", outcome="")
    out += ["## 7 derniers jours", ""]
    out += [_fmt_day(d) for d in recent] or ["- (rien de déclaré)"]
    out.append("")

    if scope == "full":
        # 2. Profile
        profile = get_accepted_profile()
        out += ["## Profil", "", profile.content.strip() if profile else "(aucun)", ""]
        # 3. Rules
        out += ["## Consignes actives", ""]
        out += [
            f"- (#{r.pk}) {r.text} — quand : {r.trigger or '?'} — message : {r.message}"
            for r in get_active_rules()
        ] or ["- (aucune)"]
        out.append("")

    # 4. Observations
    out += ["## Observations récentes", ""]
    recent_obs = list(Observation.objects.all()[:RECENT_OBSERVATIONS])
    pinned = Observation.objects.filter(pinned=True).exclude(pk__in=[o.pk for o in recent_obs])
    for obs in list(pinned) + recent_obs:
        pin = "📌 " if obs.pinned else ""
        tags = f" [{', '.join(obs.tags)}]" if obs.tags else ""
        out.append(f"- {pin}(#{obs.pk}) {obs.date:%d/%m} {obs.text}{tags}")
    if not recent_obs and not pinned:
        out.append("- (aucune)")
    out.append("")

    # 5. Events
    out += [f"## Événements ({HORIZON_DAYS} jours)", ""]
    events = Event.objects.filter(
        Q(start_date__gte=ref_date, start_date__lte=horizon)
        | Q(start_date__lt=ref_date, end_date__gte=ref_date)
    ).exclude(status=Event.Status.CANCELLED)
    for event in events:
        todo = [i.get("label", "?") for i in event.checklist if not i.get("done")]
        rest = f" — reste : {', '.join(todo)}" if todo else ""
        out.append(
            f"- (#{event.pk}) {event.start_date:%d/%m} {event.title} ({event.status}){rest}"
        )
    if not events:
        out.append("- (aucun)")
    out.append("")

    # 6. Tasks
    base = Task.objects.select_related("project")
    due = base.filter(
        due_date__lte=horizon,
        status__in=[Task.Status.INBOX, Task.Status.TODO, Task.Status.DOING, Task.Status.WAITING],
    ).order_by("due_date", "id")[:MAX_LIST]
    out += [f"## Échéances ({HORIZON_DAYS} jours, retards inclus)", ""]
    out += [_fmt_task(t) for t in due] or ["- (aucune)"]
    out += ["", "## En cours", ""]
    out += [_fmt_task(t) for t in base.filter(status=Task.Status.DOING)[:MAX_LIST]] or [
        "- (aucune)"
    ]
    stale = base.filter(
        status=Task.Status.WAITING,
        waiting_since__lte=ref_date - timedelta(days=WAITING_DAYS),
    )[:MAX_LIST]
    out += ["", f"## En attente depuis plus de {WAITING_DAYS} jours", ""]
    out += [_fmt_task(t) for t in stale] or ["- (aucune)"]

    counts = task_status_counts()
    raw_count = (
        Task.objects.filter(clarity=Task.Clarity.RAW)
        .exclude(status__in=[Task.Status.DONE, Task.Status.DROPPED])
        .count()
    )
    out += [
        "",
        "## Compteurs",
        "",
        f"- inbox : {counts['inbox']} · brutes (raw) : {raw_count} · todo : {counts['todo']} "
        f"· waiting : {counts['waiting']} · someday : {counts['someday']}",
        "",
        f"## Éléments à clarifier (extrait de {UNCLEAR_SAMPLE} : priorité du projet, puis "
        "ancienneté)",
        "",
    ]
    unclear = (
        base.filter(clarity__in=[Task.Clarity.RAW, Task.Clarity.PARTIAL])
        .exclude(status__in=[Task.Status.DONE, Task.Status.DROPPED])
        .order_by(F("project__priority").asc(nulls_last=True), "created_at", "id")
    )[:UNCLEAR_SAMPLE]
    out += [f"{_fmt_task(t)} · {t.clarity}" for t in unclear] or ["- (aucun)"]
    out += [
        "",
        "## À faire par projet (extrait, ordre échéance puis création)",
        "",
    ]
    for project in Project.objects.all():
        todos = base.filter(project=project, status=Task.Status.TODO).order_by("due_date", "id")[
            :TODO_PER_PROJECT
        ]
        if todos:
            out.append(f"### {project.name}")
            out += [_fmt_task(t) for t in todos]
    out.append("")

    # 7. Week counters (declared days per project; targets live in the profile)
    week_start = ref_date - timedelta(days=ref_date.weekday())
    week_days = list(DayLog.objects.filter(date__gte=week_start, date__lte=ref_date))
    out += ["## Semaine en cours : jours déclarés par projet", ""]
    for project in Project.objects.all():
        names = (project.slug.lower(), project.name.lower())
        n = sum(1 for d in week_days if any(x in d.declared_focus.lower() for x in names))
        out.append(f"- {project.name} : {n}")
    out.append("")
    return "\n".join(out)
