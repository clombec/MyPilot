"""
test_handlers.py

Tests for the bot logic without Discord: authorization, commands that work
without Claude, proposals, briefing format versions and message splitting.
"""

from datetime import time, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from bot import handlers
from bot.config import BotConfig
from bot.text import split_message
from core import agent_tools, services
from core.models import (
    Briefing,
    BriefingSpecVersion,
    ConversationTurn,
    Observation,
    Proposal,
    Rule,
    Task,
)

TZ = ZoneInfo("Europe/Paris")
CONFIG = BotConfig(
    token="x",
    owner_id=1,
    guild_id=2,
    channel_briefing=10,
    channel_agent=11,
    channel_capture=12,
    channel_proposals=13,
    briefing_time=time(7, 30, tzinfo=TZ),
    nudge_time=time(10, 0, tzinfo=TZ),
    export_time=time(3, 0, tzinfo=TZ),
    timezone=TZ,
    agent_webhook_ids=frozenset({77}),
)


class AuthorizationTests(TestCase):
    def test_owner_in_configured_channel_only(self) -> None:
        self.assertTrue(handlers.is_authorized(CONFIG, 1, 11))
        self.assertFalse(handlers.is_authorized(CONFIG, 999, 11))  # other user ignored
        self.assertFalse(handlers.is_authorized(CONFIG, 1, 555))  # other channel
        self.assertFalse(handlers.is_authorized(CONFIG, 1, None))  # DM

    def test_trusted_webhook_in_agent_channel_only(self) -> None:
        self.assertTrue(handlers.is_trusted_webhook(CONFIG, 77, 11))
        self.assertFalse(handlers.is_trusted_webhook(CONFIG, 78, 11))  # unknown webhook
        self.assertFalse(handlers.is_trusted_webhook(CONFIG, 77, 12))  # other channel
        self.assertFalse(handlers.is_trusted_webhook(CONFIG, None, 11))  # not a webhook


class DeterministicCommandTests(TestCase):
    """/note, /capture, /statut never call Claude: they work when it is down."""

    def test_note_records_observation(self) -> None:
        reply = handlers.note("oublié mes clés")
        self.assertIn("Observation", reply)
        self.assertEqual(Observation.objects.get().text, "oublié mes clés")

    def test_capture_with_project_prefix(self) -> None:
        reply = handlers.capture("clab: relancer la banque")
        task = Task.objects.get()
        self.assertEqual((task.project.slug, task.title), ("clab", "relancer la banque"))
        self.assertIn(f"#{task.pk}", reply)

    def test_capture_unknown_prefix_kept_as_text(self) -> None:
        handlers.capture("Note: penser au plexi")
        task = Task.objects.get()
        self.assertIsNone(task.project)
        self.assertEqual(task.title, "Note: penser au plexi")

    def test_capture_empty_and_unknown_project(self) -> None:
        self.assertIn("vide", handlers.capture("   "))
        self.assertIn("inconnu", handlers.capture("x", "nope"))
        self.assertEqual(Task.objects.count(), 0)

    def test_status(self) -> None:
        services.capture_task("a")
        Task.objects.create(title="échéance", due_date=services.today(), status="todo")
        reply = handlers.status()
        self.assertIn("inbox 1", reply)
        self.assertIn("échéance", reply)

    def test_complete_by_id(self) -> None:
        task = Task.objects.create(title="a")
        self.assertIn("Fait", handlers.complete_by_id(task.pk))
        self.assertIn("déjà", handlers.complete_by_id(task.pk))
        self.assertIn("introuvable", handlers.complete_by_id(999))

    def test_undo_reverts_agent_write(self) -> None:
        self.assertIn("Rien à annuler", handlers.undo())
        agent_tools.create_task("tâche de l'agent")
        self.assertIn("Annulé", handlers.undo())
        self.assertEqual(Task.objects.count(), 0)

    def test_declare_day(self) -> None:
        reply = handlers.declare_day("DrAB, cabinet", 6)
        self.assertIn("6 h", reply)


class ProposalFlowTests(TestCase):
    def test_rule_proposal_post_and_accept(self) -> None:
        obs = services.add_observation("oublié mes clés")
        agent_tools.propose_rule("Rappeler les clés", "les jours cabinet", "Clés !", [obs.pk])
        [pid] = handlers.pending_unposted_proposals()
        self.assertIn("Rappeler les clés", handlers.proposal_text(pid))
        handlers.mark_proposal_posted(pid, 123)
        self.assertEqual(handlers.pending_unposted_proposals(), [])
        self.assertIn("Acceptée", handlers.decide(pid, True))
        self.assertTrue(Rule.objects.get().active)
        self.assertIn("déjà traitée", handlers.decide(pid, False))

    def test_decide_missing_proposal(self) -> None:
        self.assertIn("introuvable", handlers.decide(999, True))

    def test_all_proposal_kinds_render(self) -> None:
        task = Task.objects.create(title="vieux")
        agent_tools.propose_task_drop(task.pk, "doublon")
        agent_tools.propose_profile_update("nouveau profil", "test")
        agent_tools.propose_briefing_change("1. Court", "section 3 ignorée")
        for pid in Proposal.objects.values_list("pk", flat=True):
            self.assertTrue(handlers.proposal_text(pid))


class BriefingTests(TestCase):
    def test_nudge_only_without_answer(self) -> None:
        self.assertFalse(handlers.should_nudge())
        briefing = Briefing.objects.create(date=services.today(), content="1. Bonjour")
        handlers.mark_briefing_sent(briefing.pk, 42)
        self.assertTrue(handlers.should_nudge())
        ConversationTurn.objects.create(role="user", content="DrAB 6h")
        self.assertFalse(handlers.should_nudge())

    def test_old_answer_does_not_count(self) -> None:
        turn = ConversationTurn.objects.create(role="user", content="hier")
        ConversationTurn.objects.filter(pk=turn.pk).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        Briefing.objects.create(date=services.today(), content="x", sent_at=timezone.now())
        self.assertTrue(handlers.should_nudge())

    def test_format_overview_and_restore(self) -> None:
        initial = services.get_active_briefing_spec()
        agent_tools.update_briefing_spec("1. Échéances", "échéances en premier")
        overview = handlers.briefing_format_overview()
        self.assertIn("Échéances", overview.text)
        self.assertEqual(overview.versions[0][0], initial.pk)
        reply = handlers.restore_briefing_version(initial.pk)
        self.assertIn("restauré", reply)
        active = services.get_active_briefing_spec()
        self.assertEqual(active.content, initial.content)
        self.assertEqual(BriefingSpecVersion.objects.count(), 3)  # history kept


class SplitMessageTests(TestCase):
    def test_short_text_untouched(self) -> None:
        self.assertEqual(split_message("bonjour"), ["bonjour"])
        self.assertEqual(split_message("   "), [])

    def test_long_text_split_on_lines(self) -> None:
        text = "\n".join(f"ligne {i} " + "x" * 50 for i in range(100))
        chunks = split_message(text)
        self.assertTrue(all(len(c) <= 2000 for c in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), text.replace("\n", ""))

    def test_no_break_possible(self) -> None:
        chunks = split_message("a" * 4500)
        self.assertEqual([len(c) for c in chunks], [2000, 2000, 500])
