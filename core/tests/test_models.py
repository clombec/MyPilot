"""
test_models.py

Tests for models, initial data and data services.
"""

from datetime import date, timedelta

from django.test import TestCase

from core import services
from core.models import (
    BriefingSpecVersion,
    ProfileVersion,
    Project,
    Proposal,
    ReviewStatus,
    Rule,
    Task,
)
from core.snapshot import build_snapshot


class SeedTests(TestCase):
    def test_five_projects_with_priorities(self) -> None:
        self.assertEqual(
            list(Project.objects.values_list("slug", "priority")),
            [("drab", 1), ("aure", 2), ("clab", 3), ("cabo", 4), ("dreb", 5)],
        )
        self.assertEqual(Project.objects.get(slug="dreb").fixed_weekday, 1)

    def test_initial_profile_and_briefing_spec_are_active(self) -> None:
        self.assertIn("DrAB", services.get_accepted_profile().content)
        spec = services.get_active_briefing_spec()
        self.assertIn("1 200 caractères", spec.content)


class TaskTests(TestCase):
    def test_title_alone_is_enough(self) -> None:
        task = Task.objects.create(title="Appeler le plombier")
        self.assertEqual(task.status, Task.Status.INBOX)
        self.assertEqual(task.clarity, Task.Clarity.RAW)
        self.assertIsNone(task.project)

    def test_capture_keeps_raw_text(self) -> None:
        task = services.capture_task("  acheter des gants  ", "drab")
        self.assertEqual(task.title, "acheter des gants")
        self.assertEqual(task.raw_text, "  acheter des gants  ")
        self.assertEqual(task.project.slug, "drab")

    def test_capture_rejects_empty_text(self) -> None:
        with self.assertRaises(ValueError):
            services.capture_task("   ")

    def test_complete_task_sets_done_at(self) -> None:
        task = services.complete_task(Task.objects.create(title="x"))
        self.assertEqual(task.status, Task.Status.DONE)
        self.assertIsNotNone(task.done_at)

    def test_status_counts_include_zeros(self) -> None:
        Task.objects.create(title="a")
        counts = services.task_status_counts()
        self.assertEqual(counts["inbox"], 1)
        self.assertEqual(counts["done"], 0)

    def test_upcoming_due_tasks_is_a_plain_date_filter(self) -> None:
        today = services.today()
        Task.objects.create(title="loin", due_date=today + timedelta(days=30))
        Task.objects.create(title="b", due_date=today + timedelta(days=3))
        Task.objects.create(title="a", due_date=today - timedelta(days=1))
        Task.objects.create(title="fait", due_date=today, status=Task.Status.DONE)
        self.assertEqual([t.title for t in services.upcoming_due_tasks()], ["a", "b"])


class ProposalTests(TestCase):
    def test_accepting_rule_activates_it(self) -> None:
        rule = Rule.objects.create(text="clés", trigger="jours cabinet")
        proposal = Proposal.objects.create(kind=Proposal.Kind.RULE, rule=rule)
        services.decide_proposal(proposal, accept=True)
        rule.refresh_from_db()
        self.assertTrue(rule.active)
        self.assertEqual(services.get_active_rules(), [rule])

    def test_rejecting_profile_keeps_previous(self) -> None:
        current = services.get_accepted_profile()
        version = ProfileVersion.objects.create(content="nouveau")
        proposal = Proposal.objects.create(kind=Proposal.Kind.PROFILE, profile_version=version)
        services.decide_proposal(proposal, accept=False)
        self.assertEqual(services.get_accepted_profile(), current)

    def test_accepting_briefing_change_makes_it_active(self) -> None:
        version = BriefingSpecVersion.objects.create(
            content="court", source=BriefingSpecVersion.Source.AGENT_PROPOSAL
        )
        proposal = Proposal.objects.create(kind=Proposal.Kind.BRIEFING, briefing_spec=version)
        services.decide_proposal(proposal, accept=True)
        self.assertEqual(services.get_active_briefing_spec(), version)

    def test_task_drop_never_deletes(self) -> None:
        task = Task.objects.create(title="vieux truc")
        proposal = Proposal.objects.create(kind=Proposal.Kind.TASK_DROP, task=task)
        services.decide_proposal(proposal, accept=True)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.DROPPED)

    def test_cannot_decide_twice(self) -> None:
        rule = Rule.objects.create(text="x")
        proposal = Proposal.objects.create(kind=Proposal.Kind.RULE, rule=rule)
        services.decide_proposal(proposal, accept=True)
        with self.assertRaises(ValueError):
            services.decide_proposal(proposal, accept=False)
        self.assertEqual(proposal.status, ReviewStatus.ACCEPTED)


class SnapshotTests(TestCase):
    def test_snapshot_contains_main_sections(self) -> None:
        services.capture_task("raw thing")
        services.add_observation("oublié mes clés", ["clés"])
        text = build_snapshot(date(2026, 10, 25))
        for heading in ("# Situation au dimanche 25/10/2026", "## Profil", "## Consignes"):
            self.assertIn(heading, text)
        self.assertIn("oublié mes clés", text)
        self.assertIn("brutes (raw) : 1", text)

    def test_light_scope_skips_profile(self) -> None:
        self.assertNotIn("## Profil", build_snapshot(date(2026, 10, 25), scope="light"))
