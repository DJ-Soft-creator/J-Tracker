"""Verwaltung der Journal-Vorlagen aus dem Burger-Menue („Meine Templates")."""

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
    os.environ.setdefault("SECRET_KEY", "template-admin-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-template-admin")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import main
    import journal_templates


BASE_TEMPLATES = [
    {"id": "schnell", "label": "⚡ Schnell", "type": "simple", "write_to_journal": True,
     "assigned_users": [], "target_file": None},
    {"id": "aufgabe", "label": "✅ Aufgabe", "type": "simple", "write_to_journal": True,
     "project_selector": True, "assigned_users": [], "target_file": None},
    {"id": "aufgabenplaner", "label": "📅 Aufgabe planen", "type": "form", "write_to_journal": False,
     "assigned_users": [], "target_file": None, "fields": [
         {"id": "titel", "label": "Titel", "type": "text", "required": True},
         {"id": "user", "label": "Verantwortlich", "type": "user", "required": True},
         {"id": "recurrence", "label": "Wiederholung", "type": "select", "required": True,
          "options": [{"value": "once", "label": "Einmalig"}]},
         {"id": "target_date", "label": "Start ab", "type": "date", "required": True},
     ]},
    {"id": "projekt", "label": "📁 Projekt", "type": "form", "write_to_journal": False,
     "assigned_users": [], "target_file": None, "fields": [
         {"id": "titel", "label": "Titel", "type": "text"},
         {"id": "verantwortlich", "label": "Verantwortlich", "type": "user"},
     ]},
    {"id": "einkaufsliste", "label": "🛒 Einkaufsliste", "type": "simple", "write_to_journal": False,
     "keep_template_after_submit": True, "assigned_users": [], "target_file": "einkaufsliste.md"},
    {"id": "idee", "label": "💡 Idee", "type": "simple", "write_to_journal": True,
     "assigned_users": ["user-a"], "target_file": None},
]


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class TemplateAdminApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.old = (main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH)
        main.DATA_DIR = self.data_dir
        main.USERS_PATH = self.data_dir / "users.json"
        main.CONFIG_PATH = self.data_dir / "config.json"
        main.USERS_PATH.write_text(json.dumps({"users": [
            {"id": "user-a", "username": "Alex", "password": "test", "admin": True},
            {"id": "user-b", "username": "Kim", "password": "test"},
        ]}), encoding="utf-8")
        self.write_config({
            "app_version": "2.2.5",
            "ai_providers": [{"id": "openrouter", "label": "Provider", "api_key": "secret-from-file"}],
            "templates": [dict(item) for item in BASE_TEMPLATES],
            "shopping_defaults": {"kategorien": ["Obst"]},
        })
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = main.app.test_client()
        self.login("user-a")

    def tearDown(self):
        main.DATA_DIR, main.USERS_PATH, main.CONFIG_PATH = self.old
        self.temp.cleanup()

    # ─── Helfer ──────────────────────────────────────────────────────
    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "csrf-test"

    def write_config(self, config):
        main.CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_config(self):
        return json.loads(main.CONFIG_PATH.read_text(encoding="utf-8"))

    @property
    def headers(self):
        return {"X-CSRF-Token": "csrf-test"}

    def put(self, templates):
        return self.client.put("/api/settings/templates", headers=self.headers,
                               json={"templates": templates})

    # ─── Lesen ───────────────────────────────────────────────────────
    def test_get_returns_raw_templates_and_admin_flag(self):
        response = self.client.get("/api/settings/templates")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["can_edit"])
        self.assertEqual([item["id"] for item in data["templates"]],
                         [item["id"] for item in BASE_TEMPLATES])
        self.assertIn("schnell", data["protected_ids"])
        self.assertEqual({user["id"] for user in data["users"]}, {"user-a", "user-b"})

    def test_non_admin_may_read_but_not_write(self):
        self.login("user-b")
        self.assertTrue(self.client.get("/api/settings/templates").get_json()["can_edit"] is False)
        response = self.put([dict(item) for item in BASE_TEMPLATES])
        self.assertEqual(response.status_code, 403)

    def test_write_requires_a_csrf_token(self):
        response = self.client.put("/api/settings/templates", json={"templates": BASE_TEMPLATES})
        self.assertEqual(response.status_code, 403)

    # ─── Schreiben ───────────────────────────────────────────────────
    def test_saving_touches_only_the_templates_key(self):
        templates = [dict(item) for item in BASE_TEMPLATES]
        templates[-1]["label"] = "💡 Einfall"
        response = self.put(templates)
        self.assertEqual(response.status_code, 200)
        config = self.read_config()
        self.assertEqual(config["ai_providers"][0]["api_key"], "secret-from-file")
        self.assertEqual(config["shopping_defaults"], {"kategorien": ["Obst"]})
        self.assertEqual(config["app_version"], "2.2.5")
        self.assertEqual(config["templates"][-1]["label"], "💡 Einfall")

    def test_env_injected_values_are_never_written_back(self):
        """load_config() loest {locale_LLM_IP} und AI_KEY_* auf — die Datei nicht."""
        config = self.read_config()
        config["ai_providers"][0]["api_url"] = "http://{locale_LLM_IP}:1234/v1"
        self.write_config(config)
        old_ip = main.LOCALE_LLM_IP
        main.LOCALE_LLM_IP = "192.168.0.9"
        try:
            self.assertEqual(self.put([dict(item) for item in BASE_TEMPLATES]).status_code, 200)
        finally:
            main.LOCALE_LLM_IP = old_ip
        self.assertEqual(self.read_config()["ai_providers"][0]["api_url"],
                         "http://{locale_LLM_IP}:1234/v1")

    def test_new_template_is_added_and_visible_to_the_write_tab(self):
        templates = [dict(item) for item in BASE_TEMPLATES]
        templates.append({
            "id": "lesenotiz", "label": "📖 Lesenotiz", "type": "simple",
            "heading": "Lesenotiz", "assigned_users": ["user-b"], "target_file": None,
        })
        self.assertEqual(self.put(templates).status_code, 200)
        stored = self.read_config()["templates"][-1]
        self.assertEqual(stored["id"], "lesenotiz")
        self.assertEqual(stored["heading"], "Lesenotiz")
        self.assertEqual(stored["write_to_journal"], True)
        config = self.client.get("/api/config").get_json()
        self.assertIn("lesenotiz", [item["id"] for item in config["templates"]])

    def test_reordering_survives_the_round_trip(self):
        templates = [dict(item) for item in BASE_TEMPLATES]
        templates.insert(0, templates.pop())
        self.assertEqual(self.put(templates).status_code, 200)
        self.assertEqual(self.read_config()["templates"][0]["id"], "idee")

    def test_deleting_a_free_template_works(self):
        templates = [item for item in BASE_TEMPLATES if item["id"] != "idee"]
        self.assertEqual(self.put([dict(item) for item in templates]).status_code, 200)
        self.assertNotIn("idee", [item["id"] for item in self.read_config()["templates"]])

    # ─── Schutz der verdrahteten Vorlagen ────────────────────────────
    def test_protected_template_cannot_be_deleted(self):
        templates = [dict(item) for item in BASE_TEMPLATES if item["id"] != "einkaufsliste"]
        response = self.put(templates)
        self.assertEqual(response.status_code, 400)
        self.assertIn("einkaufsliste", response.get_json()["error"])
        self.assertEqual(len(self.read_config()["templates"]), len(BASE_TEMPLATES))

    def test_protected_template_keeps_its_type_and_target_file(self):
        templates = [dict(item) for item in BASE_TEMPLATES]
        templates[4] = dict(templates[4], target_file="anders.md")
        self.assertEqual(self.put(templates).status_code, 400)

    def test_planner_template_keeps_the_fields_the_code_reads(self):
        templates = [dict(item) for item in BASE_TEMPLATES]
        templates[2] = dict(templates[2], fields=[
            {"id": "titel", "label": "Titel", "type": "text"},
        ])
        response = self.put(templates)
        self.assertEqual(response.status_code, 400)
        self.assertIn("recurrence", response.get_json()["error"])

    # ─── Validierung ─────────────────────────────────────────────────
    def test_invalid_payloads_are_rejected_without_touching_the_file(self):
        before = main.CONFIG_PATH.read_text(encoding="utf-8")
        cases = [
            ("doppelte ID", BASE_TEMPLATES + [dict(BASE_TEMPLATES[-1])]),
            ("unbekannter Typ", [dict(item) for item in BASE_TEMPLATES[:-1]]
                + [dict(BASE_TEMPLATES[-1], type="magic")]),
            ("leere Beschriftung", [dict(item) for item in BASE_TEMPLATES[:-1]]
                + [dict(BASE_TEMPLATES[-1], label="  ")]),
            ("unbekannter Benutzer", [dict(item) for item in BASE_TEMPLATES[:-1]]
                + [dict(BASE_TEMPLATES[-1], assigned_users=["ghost"])]),
            ("Pfad in target_file", [dict(item) for item in BASE_TEMPLATES[:-1]]
                + [dict(BASE_TEMPLATES[-1], target_file="../../etc/passwd.md")]),
            ("Formular ohne Felder", [dict(item) for item in BASE_TEMPLATES[:-1]]
                + [dict(BASE_TEMPLATES[-1], type="form", fields=[])]),
            ("leere Liste", []),
            ("kein Array", {"id": "x"}),
        ]
        for name, payload in cases:
            with self.subTest(name):
                response = self.put(payload)
                self.assertEqual(response.status_code, 400, name)
                self.assertTrue(response.get_json()["error"])
        self.assertEqual(main.CONFIG_PATH.read_text(encoding="utf-8"), before)

    def test_field_ids_stay_within_the_markup_safe_alphabet(self):
        templates = [dict(item) for item in BASE_TEMPLATES[:-1]] + [dict(
            BASE_TEMPLATES[-1], type="form",
            fields=[{"id": 'x" onfocus="alert(1)', "label": "X", "type": "text"}],
        )]
        self.assertEqual(self.put(templates).status_code, 400)

    def test_select_field_needs_options_and_keeps_their_shape(self):
        broken = [dict(item) for item in BASE_TEMPLATES[:-1]] + [dict(
            BASE_TEMPLATES[-1], type="form",
            fields=[{"id": "wahl", "label": "Wahl", "type": "select", "options": []}],
        )]
        self.assertEqual(self.put(broken).status_code, 400)
        good = [dict(item) for item in BASE_TEMPLATES[:-1]] + [dict(
            BASE_TEMPLATES[-1], type="form",
            fields=[{"id": "wahl", "label": "Wahl", "type": "select",
                     "options": [{"value": "a"}, {"value": "b", "label": "B"}]}],
        )]
        self.assertEqual(self.put(good).status_code, 200)
        stored = self.read_config()["templates"][-1]["fields"][0]["options"]
        self.assertEqual(stored, [{"value": "a", "label": "a"}, {"value": "b", "label": "B"}])

    def test_unknown_keys_are_dropped_so_the_shape_stays_canonical(self):
        templates = [dict(item) for item in BASE_TEMPLATES[:-1]] + [
            dict(BASE_TEMPLATES[-1], assigned_user=[], surprise="x")
        ]
        self.assertEqual(self.put(templates).status_code, 200)
        stored = self.read_config()["templates"][-1]
        self.assertNotIn("assigned_user", stored)
        self.assertNotIn("surprise", stored)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class TemplateHeadingTests(unittest.TestCase):
    """Neu angelegte einfache Vorlagen schreiben mit Ueberschrift ins Journal."""

    def test_heading_is_used_when_no_formatter_is_wired_in_code(self):
        entry = main._write_markdown_entry(
            "lesenotiz", "Text", "10:00:00", "2026-08-25 10:00:00", heading="Lesenotiz",
        )
        self.assertIn("## Lesenotiz | Datum & Uhrzeit: 2026-08-25 10:00:00", entry)

    def test_wired_formatter_wins_over_a_configured_heading(self):
        entry = main._write_markdown_entry(
            "idee", "Text", "10:00:00", "2026-08-25 10:00:00", heading="Etwas anderes",
        )
        self.assertIn("## Idee | Datum & Uhrzeit:", entry)
        self.assertNotIn("Etwas anderes", entry)

    def test_without_a_heading_the_entry_keeps_its_bare_form(self):
        entry = main._write_markdown_entry("frei", "Text", "10:00:00", "2026-08-25 10:00:00")
        self.assertEqual(entry, "___\n\nText\n\n___\n\n")


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class TemplateValidationUnitTests(unittest.TestCase):
    def test_heading_is_rejected_for_form_templates(self):
        with self.assertRaises(journal_templates.TemplateError):
            journal_templates.validate(
                [{"id": "x", "label": "X", "type": "form", "heading": "Y",
                  "fields": [{"id": "a", "label": "A", "type": "text"}]}],
                set(), [],
            )

    def test_number_bounds_must_be_ordered(self):
        with self.assertRaises(journal_templates.TemplateError):
            journal_templates.validate(
                [{"id": "x", "label": "X", "type": "form", "fields": [
                    {"id": "a", "label": "A", "type": "number", "min": 10, "max": 1}]}],
                set(), [],
            )

    def test_hashtag_template_keeps_its_tags(self):
        result = journal_templates.validate(
            [{"id": "x", "label": "X", "type": "simple",
              "hashtag_template": True, "hashtags": ["#aufgabe", "test"]}],
            set(), [],
        )
        self.assertEqual(result[0]["hashtags"], ["#aufgabe", "test"])


if __name__ == "__main__":
    unittest.main()
