"""
views.py

HTTP layer of the web interface (mobile first, HTMX).
Views only filter and sort; data operations go through core.services.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core import services
from core.models import Briefing, Note, Project, Task

from .forms import CaptureForm, NoteForm, QuickNoteForm, TaskForm

logger = logging.getLogger(__name__)

TASK_PAGE_SIZE = 200
TASK_ORDERS = {"created": ("created_at", "id"), "due": ("due_date", "id")}


def _is_htmx(request: HttpRequest) -> bool:
    """Return True when the request was sent by HTMX (partial rendering)."""
    return request.headers.get("HX-Request") == "true"


@login_required
@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """Home page: latest briefing, upcoming due dates, quick capture and note."""
    context = {
        "briefing": Briefing.objects.first(),
        "due_tasks": services.upcoming_due_tasks(days=14, limit=10),
        "counts": services.task_status_counts(),
        "capture_form": CaptureForm(),
        "note_form": QuickNoteForm(),
        "active_tab": "home",
    }
    return render(request, "web/home.html", context)


@login_required
@require_POST
def capture(request: HttpRequest) -> HttpResponse:
    """Create a raw inbox task from the capture form."""
    form = CaptureForm(request.POST)
    if form.is_valid():
        project = form.cleaned_data["project"]
        task = services.capture_task(form.cleaned_data["text"], project.slug if project else None)
        messages.success(request, f"Tâche #{task.pk} capturée.")
    else:
        messages.error(request, "Capture vide : rien n'a été enregistré.")
    return redirect(request.POST.get("next") or "web:home")


@login_required
@require_POST
def quick_note(request: HttpRequest) -> HttpResponse:
    """Append an observation from the quick note form."""
    form = QuickNoteForm(request.POST)
    if form.is_valid():
        obs = services.add_observation(form.cleaned_data["text"])
        messages.success(request, f"Observation #{obs.pk} enregistrée.")
    else:
        messages.error(request, "Observation vide : rien n'a été enregistré.")
    return redirect("web:home")


@login_required
@require_GET
def task_list(request: HttpRequest) -> HttpResponse:
    """Filterable task list. Returns only the list fragment for HTMX requests."""
    project = request.GET.get("project", "")
    status = request.GET.get("status", "open")
    clarity = request.GET.get("clarity", "")
    query = request.GET.get("q", "").strip()
    order = request.GET.get("order", "created")

    qs = Task.objects.select_related("project")
    if project == "none":
        qs = qs.filter(project__isnull=True)
    elif project:
        qs = qs.filter(project__slug=project)
    if status == "open":
        qs = qs.filter(status__in=services.OPEN_STATUSES)
    elif status and status != "all":
        qs = qs.filter(status=status)
    if clarity:
        qs = qs.filter(clarity=clarity)
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(notes__icontains=query))
    qs = qs.order_by(*TASK_ORDERS.get(order, TASK_ORDERS["created"]))

    total = qs.count()
    context = {
        "tasks": qs[:TASK_PAGE_SIZE],
        "total": total,
        "truncated": total > TASK_PAGE_SIZE,
        "projects": Project.objects.all(),
        "statuses": Task.Status.choices,
        "clarities": Task.Clarity.choices,
        "filters": {
            "project": project,
            "status": status,
            "clarity": clarity,
            "q": query,
            "order": order,
        },
        "capture_form": CaptureForm(),
        "active_tab": "tasks",
    }
    template = "web/partials/task_rows.html" if _is_htmx(request) else "web/tasks.html"
    return render(request, template, context)


@login_required
@require_http_methods(["GET", "POST"])
def task_detail(request: HttpRequest, task_id: int) -> HttpResponse:
    """Show and edit one task."""
    task = get_object_or_404(Task.objects.select_related("project", "parent"), pk=task_id)
    if request.method == "POST":
        form = TaskForm(request.POST, instance=task)
        if form.is_valid():
            form.save()
            messages.success(request, "Tâche enregistrée.")
            return redirect("web:task_detail", task_id=task.pk)
    else:
        form = TaskForm(instance=task)
    context = {
        "task": task,
        "form": form,
        "subtasks": task.subtasks.all(),
        "active_tab": "tasks",
    }
    return render(request, "web/task_detail.html", context)


@login_required
@require_POST
def task_done(request: HttpRequest, task_id: int) -> HttpResponse:
    """Mark a task as done. Returns the updated row for HTMX requests."""
    task = services.complete_task(get_object_or_404(Task, pk=task_id))
    if _is_htmx(request):
        return render(request, "web/partials/task_row.html", {"task": task})
    messages.success(request, f"Tâche #{task.pk} terminée.")
    return redirect("web:task_detail", task_id=task.pk)


@login_required
@require_GET
def note_list(request: HttpRequest) -> HttpResponse:
    """Reference notes, with a simple text search."""
    query = request.GET.get("q", "").strip()
    qs = Note.objects.select_related("project")
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(text__icontains=query))
    context = {"notes": qs, "q": query, "active_tab": "notes"}
    template = "web/partials/note_rows.html" if _is_htmx(request) else "web/notes.html"
    return render(request, template, context)


@login_required
@require_http_methods(["GET", "POST"])
def note_edit(request: HttpRequest, note_id: int | None = None) -> HttpResponse:
    """Create or edit a reference note."""
    note = get_object_or_404(Note, pk=note_id) if note_id else None
    if request.method == "POST":
        form = NoteForm(request.POST, instance=note)
        if form.is_valid():
            note = form.save()
            messages.success(request, "Note enregistrée.")
            return redirect("web:note_list")
    else:
        form = NoteForm(instance=note)
    context = {"form": form, "note": note, "active_tab": "notes"}
    return render(request, "web/note_edit.html", context)
