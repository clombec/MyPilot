"""
test_agent_tools.py

Tests for the agent tools (core/agent_tools.py) and undo (core/undo.py).
"""

from datetime import date

from django.test import TestCase

from core import agent_tools as tools
from core import services
from core.agent_tools import ToolError
from core.models import (
    AuditLog,
    BriefingSpecVersion,
    DayLog,
    Event,
    Note,
    Observation,
    Proposal,
    ReviewStatus,
    Rule,
    Task,
)
from core.undo import NothingToUndo, undo_last


class ReadToolTests(TestCase):
    def test_list_tasks_filters_and_hides_closed_by_default(self) -> None:
        services.capture_task("ouverte", "drab")
        Task.objects.create(title="finie", status=Task.Status.DONE)
        out = tools.list_tasks(project="drab")
        self.assertIn("ouverte", out)
        self.assertNotIn("finie", tools.list_tasks())
        self.assertIn("finie", tools.list_tasks(status="done"))

    def test_get_task_shows_raw_text_and_subtasks(self) -> None:
        parent = Task.objects.create(title="Stocks", raw_text="Gestion des stocks  ")
        Task.objects.create(title="gants", parent=parent)
        out = tools.get_task(parent.pk)
        self.assertIn("Texte d'origine : Gestion des stocks", out)
        self.assertIn("gants", out)

    def test_unknown_task_raises_tool_error(self) -> None:
        with self.assertRaises(ToolError):
            tools.get_task(9999)

    def test_unknown_project_lists_known_slugs(self) -> None:
        with self.assertRaisesMessage(ToolError, "drab"):
            tools.list_tasks(project="nope")

    def test_search_notes_and_briefing_spec(self) -> None:
        Note.objects.create(title="Facturation Mnemos", text="contention 60€", tags=["mnemos"])
        self.assertIn("contention", tools.search_notes("mnemos"))
        self.assertIn("1 200 caractères", tools.get_briefing_spec())


class WriteToolTests(TestCase):
    def test_create_task_is_audited_and_marked_agent(self) -> None:
        out = tools.create_task("Appeler Blot pour devis", project="drab", est_minutes=10)
        task = Task.objects.get()
        self.assertEqual(task.created_by, "agent")
        self.assertIn(f"#{task.pk}", out)
        entry = AuditLog.objects.get(tool="create_task")
        self.assertEqual(entry.actor, "agent")

    def test_failed_call_writes_nothing(self) -> None:
        with self.assertRaises(ToolError):
            tools.create_task("x", project="nope")
        self.assertEqual(Task.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_update_task_never_touches_raw_text(self) -> None:
        task = Task.objects.create(title="a", raw_text="original")
        with self.assertRaises(ToolError):
            tools.update_task(task.pk, {"raw_text": "hack"})
        tools.update_task(task.pk, {"title": "Titre clair", "clarity": "clear", "est_minutes": 20})
        task.refresh_from_db()
        self.assertEqual(task.raw_text, "original")
        self.assertEqual(
            (task.title, task.clarity, task.est_minutes), ("Titre clair", "clear", 20)
        )

    def test_drop_needs_a_proposal(self) -> None:
        task = Task.objects.create(title="a")
        with self.assertRaisesMessage(ToolError, "propose_task_drop"):
            tools.update_task(task.pk, {"status": "dropped"})

    def test_one_level_of_subtasks_only(self) -> None:
        top = Task.objects.create(title="top")
        child = Task.objects.create(title="child", parent=top)
        other = Task.objects.create(title="other")
        with self.assertRaises(ToolError):
            tools.update_task(other.pk, {"parent_id": child.pk})
        with self.assertRaises(ToolError):
            tools.update_task(top.pk, {"parent_id": other.pk})
        with self.assertRaises(ToolError):
            tools.split_task(child.pk, ["x"])

    def test_split_task_inherits_project(self) -> None:
        task = services.capture_task("Gestion des stocks", "drab")
        tools.split_task(task.pk, ["Protocole commande", "Protocole réception"])
        subs = list(task.subtasks.all())
        self.assertEqual(len(subs), 2)
        self.assertTrue(all(s.project.slug == "drab" for s in subs))

    def test_waiting_status_sets_waiting_since(self) -> None:
        task = Task.objects.create(title="a")
        tools.update_task(task.pk, {"status": "waiting", "waiting_for": "La Médicale"})
        task.refresh_from_db()
        self.assertEqual(task.waiting_since, services.today())

    def test_secrets_are_refused(self) -> None:
        with self.assertRaisesMessage(ToolError, "password"):
            tools.create_task("MAE mdp : hunter22")
        with self.assertRaises(ToolError):
            tools.add_observation("password=abc123")
        # A reminder about a password is not a secret.
        tools.create_task("Noter mdp MAE dans carnet bleu")
        self.assertEqual(Task.objects.count(), 1)

    def test_event_with_checklist(self) -> None:
        tools.create_event(
            "Congrès JO Paris",
            "2026-11-05",
            end_date="2026-11-08",
            project="drab",
            checklist=["Hôtel", {"label": "Train", "done": True}],
        )
        event = Event.objects.get()
        self.assertEqual(event.checklist[1], {"label": "Train", "done": True})
        with self.assertRaises(ToolError):
            tools.create_event("x", "2026-11-05", end_date="2026-11-01")

    def test_set_day_partial_update(self) -> None:
        tools.set_day("2026-10-26", focus="DrAB cabinet", hours=6)
        tools.set_day("2026-10-26", location="cabinet")
        log = DayLog.objects.get(date=date(2026, 10, 26))
        self.assertEqual((log.declared_focus, log.location), ("DrAB cabinet", "cabinet"))
        self.assertEqual(float(log.planned_hours), 6.0)

    def test_complete_task_twice_is_refused(self) -> None:
        task = Task.objects.create(title="a")
        tools.complete_task(task.pk)
        with self.assertRaises(ToolError):
            tools.complete_task(task.pk)


class ProposalToolTests(TestCase):
    def test_propose_rule_waits_for_validation(self) -> None:
        o1 = services.add_observation("oublié mes clés", ["clés"])
        o2 = services.add_observation("encore oublié mes clés", ["clés"])
        tools.propose_rule(
            "Rappeler les clés", "les jours cabinet", "Pense à tes clés", [o1.pk, o2.pk]
        )
        rule = Rule.objects.get()
        self.assertFalse(rule.active)
        self.assertEqual(services.get_active_rules(), [])
        proposal = Proposal.objects.get()
        services.decide_proposal(proposal, accept=True)
        self.assertEqual(services.get_active_rules(), [Rule.objects.get()])

    def test_propose_rule_checks_observation_ids(self) -> None:
        with self.assertRaises(ToolError):
            tools.propose_rule("x", source_observation_ids=[999])

    def test_task_drop_proposal_keeps_task(self) -> None:
        task = Task.objects.create(title="vieux")
        tools.propose_task_drop(task.pk, "doublon")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.INBOX)
        self.assertEqual(Proposal.objects.get().kind, Proposal.Kind.TASK_DROP)


class UndoTests(TestCase):
    def test_nothing_to_undo(self) -> None:
        with self.assertRaises(NothingToUndo):
            undo_last()

    def test_undo_create_deletes(self) -> None:
        tools.create_task("à annuler")
        undo_last()
        self.assertEqual(Task.objects.count(), 0)
        self.assertTrue(AuditLog.objects.get(tool="create_task").undone)

    def test_undo_update_restores_fields(self) -> None:
        task = Task.objects.create(title="a", due_date=date(2026, 11, 1))
        tools.update_task(task.pk, {"title": "b", "due_date": None, "project": "clab"})
        undo_last()
        task.refresh_from_db()
        self.assertEqual((task.title, task.due_date, task.project), ("a", date(2026, 11, 1), None))

    def test_undo_complete_reopens(self) -> None:
        task = Task.objects.create(title="a", status=Task.Status.TODO)
        tools.complete_task(task.pk)
        undo_last()
        task.refresh_from_db()
        self.assertEqual((task.status, task.done_at), ("todo", None))

    def test_undo_event_checklist_and_links(self) -> None:
        t1 = Task.objects.create(title="hôtel")
        tools.create_event("Formation", "2026-12-12", checklist=["Hôtel"], task_ids=[t1.pk])
        event = Event.objects.get()
        tools.update_event(event.pk, {"checklist": [{"label": "Hôtel", "done": True}]})
        tools.update_event(event.pk, {"task_ids": []})
        undo_last()
        undo_last()
        event.refresh_from_db()
        self.assertEqual(event.checklist, [{"label": "Hôtel", "done": False}])
        self.assertEqual(list(event.tasks.all()), [t1])

    def test_undo_set_day_on_new_day_deletes_it(self) -> None:
        tools.set_day("2026-10-27", focus="Aure")
        undo_last()
        self.assertFalse(DayLog.objects.filter(date=date(2026, 10, 27)).exists())

    def test_undo_observation_and_proposal(self) -> None:
        tools.add_observation("oublié mes clés")
        tools.propose_profile_update("nouveau profil", "test")
        undo_last()
        self.assertEqual(Proposal.objects.count(), 0)
        undo_last()
        self.assertEqual(Observation.objects.count(), 0)

    def test_briefing_spec_change_then_undo_restores_previous(self) -> None:
        """Acceptance: new spec active, old version kept, /annuler restores it."""
        initial = services.get_active_briefing_spec()
        tools.update_briefing_spec(
            "1. Échéances\n2. Rappels\nMax 600 caractères.",
            "mets les échéances en premier et raccourcis",
        )
        new = services.get_active_briefing_spec()
        self.assertNotEqual(new, initial)
        self.assertIn("Échéances", new.content)
        self.assertTrue(BriefingSpecVersion.objects.filter(pk=initial.pk).exists())
        undo_last()
        self.assertEqual(services.get_active_briefing_spec(), initial)
        new.refresh_from_db()
        self.assertEqual(new.status, ReviewStatus.REJECTED)

    def test_undo_skips_user_actions(self) -> None:
        tools.create_task("agent")
        AuditLog.objects.create(tool="decide_proposal", actor="user", undo_data={})
        undo_last()
        self.assertEqual(Task.objects.count(), 0)
