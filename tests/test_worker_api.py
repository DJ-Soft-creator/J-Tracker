import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "worker-api-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-worker-api-import")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import main
    import worker_api


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class WorkerApiTests(unittest.TestCase):
    def setUp(self):
        main.app.config.update(TESTING=True)
        self.client = main.app.test_client()
        self.token = "x" * 32
        self.env = mock.patch.dict(os.environ, {"JOURNL_WORKER_API_TOKEN": self.token})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_api_is_invisible_without_valid_server_token(self):
        with mock.patch.dict(os.environ, {"JOURNL_WORKER_API_TOKEN": ""}):
            response = self.client.post("/api/internal/worker/agent-jobs/claim", json={})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("Cache-Control"), None)

    def test_claim_and_response_require_token_and_do_not_use_browser_session(self):
        response = self.client.post("/api/internal/worker/agent-jobs/claim", json={})
        self.assertEqual(response.status_code, 404)
        job = {"job_id": "job", "lease_token": "lease", "request_text": "secret"}
        with mock.patch.object(worker_api.background_jobs, "claim_next_agent_job", return_value=job) as claim:
            response = self.client.post("/api/internal/worker/agent-jobs/claim", headers={
                "X-Journl-Worker-Token": self.token,
            }, json={"lease_seconds": 60})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"job": job})
        claim.assert_called_once_with(lease_seconds=60)
        with mock.patch.object(worker_api.background_jobs, "record_agent_response",
                               return_value={"response_id": "response", "status": "proposed"}) as record:
            response = self.client.post("/api/internal/worker/agent-jobs/job/response", headers={
                "X-Journl-Worker-Token": self.token,
            }, json={"lease_token": "lease", "response_text": "answer"})
        self.assertEqual(response.status_code, 201)
        record.assert_called_once_with("job", "lease", "answer")

    def test_image_capability_error_codes_have_user_facing_messages(self):
        unsupported = worker_api.background_jobs.agent_error_message("model_no_image_support")
        unknown = worker_api.background_jobs.agent_error_message("model_image_capability_unknown")
        self.assertIn("unterstützt keine Bildanhänge", unsupported)
        self.assertIn("nicht sicher geprüft", unknown)
        self.assertEqual(worker_api.background_jobs.agent_error_message("other_error"), "other_error")


if __name__ == "__main__":
    unittest.main()
