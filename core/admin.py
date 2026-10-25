"""
admin.py

Django admin registration: a fallback editing interface for every model.
"""

from django.contrib import admin

from . import models


@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "priority", "fixed_weekday")


@admin.register(models.Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "project", "status", "clarity", "due_date", "created_by")
    list_filter = ("status", "clarity", "project", "created_by")
    search_fields = ("title", "raw_text", "notes")
    readonly_fields = ("raw_text", "created_at", "updated_at")


@admin.register(models.Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "start_date", "end_date", "status")
    list_filter = ("status", "project")


@admin.register(models.Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ("date", "text", "source", "pinned")
    list_filter = ("source", "pinned")


@admin.register(models.Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ("text", "trigger", "status", "active")
    list_filter = ("status", "active")


@admin.register(models.Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "status", "created_at", "decided_at")
    list_filter = ("kind", "status")


@admin.register(models.AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tool", "actor", "undone")
    list_filter = ("tool", "actor", "undone")


for _model in (
    models.DayLog,
    models.WorkSession,
    models.Note,
    models.ProfileVersion,
    models.BriefingSpecVersion,
    models.Briefing,
    models.ConversationTurn,
):
    admin.site.register(_model)
