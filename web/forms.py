"""
forms.py

Forms of the web interface. Labels are in French (user-facing).
"""

from django import forms

from core.models import Note, Project, Task


class CaptureForm(forms.Form):
    """Quick capture of a raw task."""

    text = forms.CharField(label="Tâche", max_length=2000)
    project = forms.ModelChoiceField(
        Project.objects.all(), required=False, label="Projet", to_field_name="slug"
    )


class QuickNoteForm(forms.Form):
    """Quick observation (same as `/note` on Discord)."""

    text = forms.CharField(label="Observation", max_length=1000)


class TaskForm(forms.ModelForm):
    """Task edition. `raw_text` is shown read-only in the template, never edited."""

    class Meta:
        model = Task
        fields = [
            "title",
            "project",
            "status",
            "clarity",
            "notes",
            "est_minutes",
            "energy",
            "context",
            "due_date",
            "not_before",
            "waiting_for",
            "waiting_since",
            "parent",
            "linked_project",
            "recurrence",
        ]
        labels = {
            "title": "Titre",
            "project": "Projet",
            "status": "Statut",
            "clarity": "Clarté",
            "notes": "Notes",
            "est_minutes": "Durée estimée (min)",
            "energy": "Énergie",
            "context": "Contexte",
            "due_date": "Échéance",
            "not_before": "Pas avant le",
            "waiting_for": "En attente de",
            "waiting_since": "En attente depuis",
            "parent": "Chantier parent",
            "linked_project": "Projet lié",
            "recurrence": "Récurrence",
        }
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "not_before": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "waiting_since": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # A parent is a top-level "chantier": only one level of nesting.
        parents = Task.objects.filter(parent__isnull=True).exclude(
            status__in=[Task.Status.DONE, Task.Status.DROPPED]
        )
        if self.instance.pk:
            parents = parents.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = parents


class NoteForm(forms.ModelForm):
    """Reference note edition."""

    tags_text = forms.CharField(label="Étiquettes (séparées par des virgules)", required=False)

    class Meta:
        model = Note
        fields = ["title", "project", "text"]
        labels = {"title": "Titre", "project": "Projet", "text": "Texte"}
        widgets = {"text": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["tags_text"].initial = ", ".join(self.instance.tags)

    def save(self, commit: bool = True) -> Note:
        note = super().save(commit=False)
        raw = self.cleaned_data.get("tags_text", "")
        note.tags = [t.strip() for t in raw.split(",") if t.strip()]
        if commit:
            note.save()
        return note
