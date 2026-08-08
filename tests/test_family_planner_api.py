import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "planner-api-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/opencode/journl-planner-api-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import family
    import main


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class FamilyPlannerApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        main.DATA_DIR = data_dir
        main.USERS_PATH = data_dir / "users.json"
        family.DATA_DIR = data_dir
        family.FAMILY_DIR = data_dir / "family"
        family.PROJECTS_DIR = family.FAMILY_DIR / "projects"
        family.PLANNER_DIR = family.FAMILY_DIR / "planner"
        family.ARCHIVE_DIR = family.FAMILY_DIR / "archive"
        family.PLANNER_FILE = family.PLANNER_DIR / "recurring.md"
        family.FAMILY_TASKS_FILE = family.FAMILY_DIR / "Familien-Aufgaben.md"
        data_dir.mkdir(parents=True, exist_ok=True)
        main.USERS_PATH.write_text(json.dumps({
            "users": [
                {"id": "user-a", "username": "TestuserA", "password": "test"},
                {"id": "user-b", "username": "TestuserB", "password": "test"},
            ]
        }), encoding="utf-8")
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        self._login("user-a")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "csrf-test"

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def test_create_materialize_list_and_complete_plan(self):
        today = main.get_tz_aware_now()[0].date().isoformat()
        response = self.client.post("/api/family/planner", headers=self.headers, json={
            "title": "Müll rausbringen",
            "user": "user-b",
            "recurrence": "daily",
            "start_date": today,
            "active": True,
        })

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["scheduler"]["added"], 1)
        task_id = payload["scheduler"]["task_ids"][0]

        planner = self.client.get("/api/family/planner").get_json()
        self.assertEqual(len(planner["items"]), 1)
        self.assertEqual(planner["items"][0]["next_due_date"], today)

        projects = self.client.get("/api/family/projects").get_json()["projects"]
        scheduled_project = next(item for item in projects if item["id"] == family.RECURRING_PROJECT_ID)
        self.assertEqual([task["id"] for task in scheduled_project["tasks"]], [task_id])

        first = self.client.post("/api/family/task/check", headers=self.headers, json={
            "project_id": family.RECURRING_PROJECT_ID,
            "task_id": task_id,
            "completed": True,
        })
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.get_json()["already_completed"])

        self._login("user-b")
        second = self.client.post("/api/family/task/check", headers=self.headers, json={
            "project_id": family.RECURRING_PROJECT_ID,
            "task_id": task_id,
            "completed": True,
        })
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["already_completed"])
        task = family.parse_recurring_tasks()[0]
        self.assertEqual(task["completed_by"], "user-a")

        reopened = self.client.post("/api/family/task/check", headers=self.headers, json={
            "project_id": family.RECURRING_PROJECT_ID,
            "task_id": task_id,
            "completed": False,
        })
        self.assertEqual(reopened.status_code, 200)
        self.assertFalse(reopened.get_json()["completed"])
        task = family.parse_recurring_tasks()[0]
        self.assertFalse(task["completed"])
        self.assertIsNone(task["completed_at"])
        self.assertIsNone(task["completed_by"])

    def test_update_pause_and_delete_plan(self):
        today = main.get_tz_aware_now()[0].date().isoformat()
        created = self.client.post("/api/family/planner", headers=self.headers, json={
            "title": "Aufgabe erledigen",
            "user": "user-a",
            "recurrence": "weekly_saturday",
            "start_date": today,
            "active": True,
        }).get_json()
        plan_id = created["item"]["id"]

        paused = self.client.put(
            f"/api/family/planner/{plan_id}",
            headers=self.headers,
            json={"active": False},
        )
        self.assertEqual(paused.status_code, 200)
        self.assertFalse(paused.get_json()["item"]["active"])

        deleted = self.client.delete(f"/api/family/planner/{plan_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/family/planner").get_json()["items"], [])

    def test_invalid_recurrence_is_rejected_without_file_entry(self):
        today = main.get_tz_aware_now()[0].date().isoformat()
        response = self.client.post("/api/family/planner", headers=self.headers, json={
            "title": "Ungültiger Plan",
            "user": "user-a",
            "recurrence": "irgendwann",
            "start_date": today,
            "active": True,
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(family.parse_planner(family._read_file(family.PLANNER_FILE) or ""), [])


if __name__ == "__main__":
    unittest.main()
