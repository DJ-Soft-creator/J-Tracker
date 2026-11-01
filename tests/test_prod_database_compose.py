import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductionDatabaseComposeTests(unittest.TestCase):
    def test_database_service_is_opt_in_and_has_no_host_port(self):
        content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        postgres = content.split("  postgres:\n", 1)[1].split("  whisper:", 1)[0]
        self.assertIn('profiles: ["database"]', postgres)
        self.assertNotIn("ports:", postgres)
        self.assertIn("postgres-prod-data:/var/lib/postgresql/data", postgres)

    def test_journal_analysis_scheduler_is_database_profile_only(self):
        content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        service = content.split("  journal-analysis-scheduler:\n", 1)[1].split("  whisper-scheduler:\n", 1)[0]
        self.assertIn('profiles: ["database"]', service)
        self.assertNotIn("ports:", service)
        self.assertIn("--loop", service)

    def test_existing_app_is_not_forced_to_start_database(self):
        content = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        app = content.split("  journaling-tracker:\n", 1)[1].split("  family-scheduler:", 1)[0]
        self.assertNotIn("depends_on:", app)
        self.assertIn("JOURNL_DB_LOCK_ROOT=/runtime-locks", app)


if __name__ == "__main__":
    unittest.main()
