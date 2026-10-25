"""
test_views.py

Tests for the web interface: login required, capture, quick note, task list.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Note, Observation, Task


class WebTests(TestCase):
    def setUp(self) -> None:
        user = get_user_model().objects.create_user("moi", password="mot-de-passe-long")
        self.client.force_login(user)

    def test_login_required(self) -> None:
        self.client.logout()
        response = self.client.get(reverse("web:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("web:login"), response["Location"])

    def test_home_renders(self) -> None:
        response = self.client.get(reverse("web:home"))
        self.assertContains(response, "Capturer")

    def test_capture_creates_raw_task(self) -> None:
        self.client.post(reverse("web:capture"), {"text": "acheter gants", "project": "drab"})
        task = Task.objects.get()
        self.assertEqual(task.project.slug, "drab")
        self.assertEqual(task.clarity, Task.Clarity.RAW)

    def test_quick_note_creates_observation(self) -> None:
        self.client.post(reverse("web:quick_note"), {"text": "oublié mes clés"})
        self.assertEqual(Observation.objects.get().text, "oublié mes clés")

    def test_task_list_filters_and_htmx_partial(self) -> None:
        Task.objects.create(title="ouverte")
        Task.objects.create(title="finie", status=Task.Status.DONE)
        page = self.client.get(reverse("web:task_list"))
        self.assertContains(page, "ouverte")
        self.assertNotContains(page, "finie")
        partial = self.client.get(
            reverse("web:task_list"), {"status": "all"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(partial, "finie")
        self.assertNotContains(partial, "<html")

    def test_task_done_via_htmx(self) -> None:
        task = Task.objects.create(title="a")
        response = self.client.post(
            reverse("web:task_done", args=[task.pk]), HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, 'class="done"')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.DONE)

    def test_task_edit_keeps_raw_text(self) -> None:
        task = Task.objects.create(title="a", raw_text="original")
        self.client.post(
            reverse("web:task_detail", args=[task.pk]),
            {"title": "b", "status": "todo", "clarity": "partial", "raw_text": "hack"},
        )
        task.refresh_from_db()
        self.assertEqual((task.title, task.raw_text), ("b", "original"))

    def test_note_create_and_search(self) -> None:
        self.client.post(
            reverse("web:note_create"),
            {"title": "Facturation", "text": "règle Mnemos", "tags_text": "mnemos, cabinet"},
        )
        self.assertEqual(Note.objects.get().tags, ["mnemos", "cabinet"])
        response = self.client.get(reverse("web:note_list"), {"q": "Mnemos"})
        self.assertContains(response, "Facturation")
