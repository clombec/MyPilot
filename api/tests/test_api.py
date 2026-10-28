"""
test_api.py

Tests for the REST API: token protection and basic CRUD.
"""

from django.test import TestCase, override_settings

from core.models import Observation, Task

TOKEN = "test-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


@override_settings(API_TOKEN=TOKEN)
class AuthTests(TestCase):
    def test_missing_token_is_refused(self) -> None:
        self.assertEqual(self.client.get("/api/tasks").status_code, 401)

    def test_wrong_token_is_refused(self) -> None:
        response = self.client.get("/api/tasks", HTTP_AUTHORIZATION="Bearer nope")
        self.assertEqual(response.status_code, 401)

    @override_settings(API_TOKEN="")
    def test_empty_configured_token_refuses_everything(self) -> None:
        response = self.client.get("/api/tasks", HTTP_AUTHORIZATION="Bearer ")
        self.assertEqual(response.status_code, 401)


@override_settings(API_TOKEN=TOKEN)
class TaskApiTests(TestCase):
    def test_create_and_filter(self) -> None:
        response = self.client.post(
            "/api/tasks",
            {"title": "Relancer Kolecto", "project": "clab"},
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["project_slug"], "clab")
        self.assertEqual(data["raw_text"], "Relancer Kolecto")

        listed = self.client.get("/api/tasks?project=clab&status=inbox", **AUTH).json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(self.client.get("/api/tasks?project=drab", **AUTH).json(), [])

    def test_patch_never_touches_raw_text(self) -> None:
        task = Task.objects.create(title="a", raw_text="original")
        response = self.client.patch(
            f"/api/tasks/{task.pk}",
            {"title": "b", "raw_text": "hack", "status": "todo"},
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.title, "b")
        self.assertEqual(task.raw_text, "original")

    def test_unknown_project_gives_400(self) -> None:
        response = self.client.post(
            "/api/tasks",
            {"title": "x", "project": "nope"},
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(response.status_code, 400)

    def test_done_endpoint(self) -> None:
        task = Task.objects.create(title="a")
        data = self.client.post(f"/api/tasks/{task.pk}/done", **AUTH).json()
        self.assertEqual(data["status"], "done")
        self.assertIsNotNone(data["done_at"])

    def test_disallowed_order_gives_400(self) -> None:
        self.assertEqual(self.client.get("/api/tasks?order=title", **AUTH).status_code, 400)


@override_settings(API_TOKEN=TOKEN)
class OtherApiTests(TestCase):
    def test_observation_create_and_pin_only(self) -> None:
        response = self.client.post(
            "/api/observations",
            {"text": "oublié mes clés", "tags": ["clés"]},
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(response.status_code, 201)
        obs_id = response.json()["id"]
        self.client.patch(
            f"/api/observations/{obs_id}",
            {"pinned": True, "text": "modifié"},
            content_type="application/json",
            **AUTH,
        )
        obs = Observation.objects.get(pk=obs_id)
        self.assertTrue(obs.pinned)
        self.assertEqual(obs.text, "oublié mes clés")
        tagged = self.client.get("/api/observations?tag=clés", **AUTH).json()
        self.assertEqual(len(tagged), 1)

    def test_event_with_checklist_and_tasks(self) -> None:
        task = Task.objects.create(title="réserver hôtel")
        response = self.client.post(
            "/api/events",
            {
                "title": "Congrès",
                "start_date": "2026-11-20",
                "checklist": [{"label": "hôtel", "done": False}],
                "task_ids": [task.pk],
            },
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["task_ids"], [task.pk])

    def test_day_upsert(self) -> None:
        url = "/api/days/2026-10-25"
        self.client.put(url, {"declared_focus": "DrAB"}, content_type="application/json", **AUTH)
        self.client.put(url, {"location": "cabinet"}, content_type="application/json", **AUTH)
        data = self.client.get(url, **AUTH).json()
        self.assertEqual(data["declared_focus"], "DrAB")
        self.assertEqual(data["location"], "cabinet")

    def test_snapshot_and_projects(self) -> None:
        self.assertIn("# Situation", self.client.get("/api/snapshot", **AUTH).json()["markdown"])
        self.assertEqual(len(self.client.get("/api/projects", **AUTH).json()), 5)

    def test_notes_search(self) -> None:
        self.client.post(
            "/api/notes",
            {"title": "Vacances", "text": "fermeture août", "tags": ["dates"]},
            content_type="application/json",
            **AUTH,
        )
        self.assertEqual(len(self.client.get("/api/notes?q=août", **AUTH).json()), 1)
        self.assertEqual(self.client.get("/api/notes?q=zzz", **AUTH).json(), [])
