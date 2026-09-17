import importlib.util
import json
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("journl_agent_worker", ROOT / "scripts/journal-agent-worker.py")
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class AgentWorkerReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        (self.allowed / "docs").mkdir()
        (self.allowed / "docs/architecture.md").write_text("# Architektur\n", encoding="utf-8")
        self.jobs = self.root / "data/user-a/ai_jobs"
        self.jobs.mkdir(parents=True)
        self.job_id = str(uuid.uuid4())
        self.job_path = self.jobs / f"{self.job_id}.json"
        self.config = self.root / "host_worker.json"
        self.config.write_text(json.dumps({
            "external_read_roots": [{"id": "docs", "path": str(self.allowed), "label": "Docs"}],
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _job(self, path):
        references = [{
            "tag": "architecture", "kind": "reference", "description": "",
            "scope": "host", "root_id": "docs", "path": str(path), "recursive": True,
        }]
        (self.jobs / f"{self.job_id}.references.json").write_text(json.dumps(references), encoding="utf-8")
        return {
            "id": self.job_id, "user_id": "user-a", "knowledge_references": references,
            "reference_manifest_path": f"user-a/ai_jobs/{self.job_id}.references.json",
        }

    def test_host_revalidates_and_resolves_reference_folder(self):
        manifest = worker.build_reference_manifest(self._job(self.allowed / "docs"), self.job_path, self.config)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data, [{
            "tag": "architecture", "kind": "reference", "description": "",
            "path": str((self.allowed / "docs").resolve()),
        }])

    def test_symlinked_reference_folder_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.allowed / "linked"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "outside an allowed host root"):
            worker.build_reference_manifest(self._job(link), self.job_path, self.config)

    def test_worker_api_is_disabled_by_default_and_requires_a_local_endpoint(self):
        self.assertEqual(worker.worker_api_config(self.config), {"enabled": False})
        self.config.write_text(json.dumps({
            "worker_api": {"enabled": False, "base_url": "http://127.0.0.1:4100", "token": "x" * 32},
        }), encoding="utf-8")
        self.assertFalse(worker.worker_api_config(self.config)["enabled"])
        self.config.write_text(json.dumps({
            "worker_api": {"enabled": True, "base_url": "https://example.test", "token": "x" * 32},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "local HTTP root"):
            worker.worker_api_config(self.config)

    def test_db_native_prompt_uses_only_the_leased_api_payload(self):
        job = {
            "job_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()),
            "owner_id": "user-a", "workflow_tag": "ai-plan", "agent_name": "pi", "model_name": "provider/model",
            "workflow_prompt": "Erstelle einen Plan.", "request_text": "Plane morgen.",
            "context_snapshot": "Referenz: Termin um 10.", "lease_token": str(uuid.uuid4()),
        }
        prompt = worker.db_native_prompt(job)
        self.assertIn("## Workflow-Auftrag\nErstelle einen Plan.", prompt)
        self.assertIn("## Benutzerauftrag\nPlane morgen.", prompt)
        self.assertIn("Referenz: Termin um 10.", prompt)
        job["agent_name"] = "custom"
        with self.assertRaisesRegex(ValueError, "not runnable"):
            worker.db_native_prompt(job)

    def test_db_native_runner_returns_answer_only_via_api(self):
        image_dir = self.root / "user-a/ai_jobs/attachments" / str(uuid.uuid4())
        image_dir.mkdir(parents=True)
        image = image_dir / "image-1.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        job = {
            "job_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()),
            "owner_id": "user-a", "workflow_tag": "ai-plan", "agent_name": "pi", "model_name": "provider/model",
            "workflow_prompt": "Erstelle einen Plan.", "request_text": "Plane morgen.",
            "context_snapshot": "", "lease_token": str(uuid.uuid4()),
            "attachment_images": [{
                "type": "image", "mime_type": "image/png",
                "path": image.relative_to(self.root).as_posix(),
                "sha256": __import__("hashlib").sha256(image.read_bytes()).hexdigest(),
                "byte_size": image.stat().st_size,
            }],
        }
        calls = []
        def api_call(_api, method, path, payload=None):
            calls.append((method, path, payload))
            return {"job": job} if path.endswith("/claim") else {"response_id": str(uuid.uuid4())}
        def runner(command, **kwargs):
            self.assertNotIn("--source", command)
            self.assertIn("--no-builtin-tools", command)
            self.assertIn("@" + str(image), command)
            self.assertIn("Erstelle einen Plan.", kwargs["input"])
            return subprocess.CompletedProcess(command, 0, "Antwort", "")
        def capabilities(command, **_kwargs):
            self.assertEqual(command, ["pi", "--offline", "--list-models", "provider/model"])
            return subprocess.CompletedProcess(
                command, 0,
                "provider  model  context  max-out  thinking  images\nprovider  model  128K  16K  yes  yes\n",
                "",
            )
        with mock.patch.object(worker, "worker_api_call", side_effect=api_call):
            self.assertTrue(worker.run_db_native_once(
                {"base_url": "http://127.0.0.1:4100", "token": "x" * 32}, self.root,
                run_command=runner, capability_command=capabilities,
            ))
        self.assertTrue(calls[-1][1].endswith("/response"), calls)
        self.assertEqual(calls[-1][2]["response_text"], "Antwort")

    def test_pi_model_catalog_parser_requires_an_exact_model(self):
        output = (
            "provider    model                 context  max-out  thinking  images\n"
            "openrouter  text/model            128K     16K      yes       no\n"
            "openrouter  vision/model          128K     16K      yes       yes\n"
        )
        self.assertFalse(worker.pi_model_image_support(output, "openrouter/text/model"))
        self.assertTrue(worker.pi_model_image_support(output, "openrouter/vision/model"))
        self.assertIsNone(worker.pi_model_image_support(output, "openrouter/missing"))

    def test_db_native_runner_rejects_image_for_text_only_model_before_pi_starts(self):
        image_dir = self.root / "user-a/ai_jobs/attachments" / str(uuid.uuid4())
        image_dir.mkdir(parents=True)
        image = image_dir / "image-1.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        job = {
            "job_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()),
            "owner_id": "user-a", "workflow_tag": "ai-plan", "agent_name": "pi",
            "model_name": "openrouter/text/model", "workflow_prompt": "Erstelle einen Plan.",
            "request_text": "Analysiere das Bild.", "context_snapshot": "", "lease_token": str(uuid.uuid4()),
            "attachment_images": [{
                "type": "image", "mime_type": "image/png",
                "path": image.relative_to(self.root).as_posix(),
                "sha256": __import__("hashlib").sha256(image.read_bytes()).hexdigest(),
                "byte_size": image.stat().st_size,
            }],
        }
        calls = []
        def api_call(_api, method, path, payload=None):
            calls.append((method, path, payload))
            return {"job": job} if path.endswith("/claim") else {"ok": True}
        def capabilities(command, **_kwargs):
            return subprocess.CompletedProcess(
                command, 0,
                "provider    model       context  max-out  thinking  images\n"
                "openrouter  text/model  128K     16K      yes       no\n",
                "",
            )
        def must_not_run(*_args, **_kwargs):
            self.fail("Pi must not start for a text-only model with an image")
        with mock.patch.object(worker, "worker_api_call", side_effect=api_call):
            self.assertTrue(worker.run_db_native_once(
                {"base_url": "http://127.0.0.1:4100", "token": "x" * 32}, self.root,
                run_command=must_not_run, capability_command=capabilities,
            ))
        self.assertTrue(calls[-1][1].endswith("/failure"))
        self.assertEqual(calls[-1][2]["error_code"], "model_no_image_support")

    def test_text_only_job_skips_model_image_capability_query(self):
        def must_not_run(*_args, **_kwargs):
            self.fail("Text-only jobs do not need an image capability query")
        worker.require_model_image_support("provider/model", [], run_command=must_not_run)

    def test_file_worker_rejects_text_only_model_before_starting_runner(self):
        data_root = self.root / "data"
        owner_root = data_root / "user-a"
        image_dir = owner_root / "ai_jobs/attachments" / str(uuid.uuid4())
        image_dir.mkdir(parents=True)
        image = image_dir / "image-1.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nimage")
        source = owner_root / "ai-agent/session.md"
        source.parent.mkdir(parents=True)
        source.write_text("# Session\n", encoding="utf-8")
        prompt = self.jobs / f"{self.job_id}.prompt.md"
        request = self.jobs / f"{self.job_id}.request.md"
        prompt.write_text("Analysiere das Bild.", encoding="utf-8")
        request.write_text("Was ist zu sehen?", encoding="utf-8")
        job = {
            "id": self.job_id, "user_id": "user-a", "username": "Alex", "status": "queued",
            "agent": "pi", "model": "openrouter/text/model", "session_id": str(uuid.uuid4()),
            "source_path": source.relative_to(data_root).as_posix(),
            "prompt_path": prompt.relative_to(data_root).as_posix(),
            "section_path": source.relative_to(data_root).as_posix(),
            "request_path": request.relative_to(data_root).as_posix(),
            "expected_revision": "", "knowledge_snapshot_paths": [],
            "attachment_images": [{
                "type": "image", "mime_type": "image/png",
                "path": image.relative_to(data_root).as_posix(),
                "sha256": __import__("hashlib").sha256(image.read_bytes()).hexdigest(),
                "byte_size": image.stat().st_size,
            }],
        }
        self.job_path.write_text(json.dumps(job), encoding="utf-8")
        catalog = subprocess.CompletedProcess(
            [], 0,
            "provider    model       context  max-out  thinking  images\n"
            "openrouter  text/model  128K     16K      yes       no\n",
            "",
        )
        with mock.patch.object(worker.subprocess, "Popen") as start:
            worker.process(
                self.job_path, data_root, ROOT / "scripts/run-agent-session.sh", self.config,
                capability_command=lambda *_args, **_kwargs: catalog,
            )
        start.assert_not_called()
        rejected = json.loads(self.job_path.read_text(encoding="utf-8"))
        self.assertEqual(rejected["status"], "error")
        self.assertIn("unterstützt keine Bildanhänge", rejected["error"])

    def test_worker_rejects_attachment_outside_the_private_snapshot_tree(self):
        outside = self.root / "user-a/not-an-ai-snapshot.png"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b"image")
        job = {
            "owner_id": "user-a",
            "attachment_images": [{
                "type": "image", "mime_type": "image/png",
                "path": outside.relative_to(self.root).as_posix(),
                "sha256": __import__("hashlib").sha256(outside.read_bytes()).hexdigest(),
                "byte_size": outside.stat().st_size,
            }],
        }
        with self.assertRaisesRegex(ValueError, "path is invalid"):
            worker.validated_attachment_images(job, self.root)


class AgentWorkerInstallerTests(unittest.TestCase):
    def test_installer_gives_the_worker_exclusive_read_access_to_its_secret_config(self):
        installer = (ROOT / "scripts/install-run-agent-session.sh").read_text(encoding="utf-8")
        self.assertIn('chown "$run_user":"$run_user" "$config_path"', installer)
        self.assertIn('chmod 0600 "$config_path"', installer)

    def test_runner_accepts_only_validated_image_attachment_files(self):
        runner = (ROOT / "scripts/run-agent-session.sh").read_text(encoding="utf-8")
        self.assertIn("--attachment-file", runner)
        self.assertIn('${attachment_files[@]/#/@}', runner)


if __name__ == "__main__":
    unittest.main()
