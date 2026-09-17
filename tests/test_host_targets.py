"""Was der Host dem Webinterface ueber Werkzeuge und Modelle meldet."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import host_targets  # noqa: E402


PI_OUTPUT = """provider    model                     context  max-out  thinking  images
lmstudio    qwen3.6-35b-a3b           131.1K   32.8K    no        no
openai      gpt-5                     400K     128K     yes       yes
anthropic   claude-opus-5             1M       128K     yes       yes
"""


class HostTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, mapping):
        def fake(command, **kwargs):
            key = command[0] if command else ""
            if key == "which":
                name = command[1]
                found = name in mapping.get("_which", [])
                return subprocess.CompletedProcess(command, 0 if found else 1,
                                                   f"/usr/bin/{name}\n" if found else "", "")
            return subprocess.CompletedProcess(command, mapping.get("code", 0),
                                               mapping.get("stdout", ""), "")
        return fake

    def test_pi_models_are_parsed_with_provider_prefix(self):
        models = host_targets.pi_models(
            run=self._run({"stdout": PI_OUTPUT, "_which": ["pi"]}))
        identifiers = [item["id"] for item in models]
        self.assertEqual(identifiers, ["lmstudio/qwen3.6-35b-a3b", "openai/gpt-5",
                                       "anthropic/claude-opus-5"])
        # Die Kopfzeile darf nicht als Modell durchgehen.
        self.assertNotIn("provider/model", identifiers)
        self.assertTrue(next(i for i in models if i["id"] == "openai/gpt-5")["thinking"])
        self.assertFalse(models[0]["thinking"])

    def test_a_missing_pi_yields_an_empty_list_instead_of_failing(self):
        def missing(command, **kwargs):
            raise FileNotFoundError("pi")
        self.assertEqual(host_targets.pi_models(run=missing), [])

    def test_refresh_writes_tool_availability(self):
        path = host_targets.refresh(
            self.root, run=self._run({"stdout": PI_OUTPUT, "_which": ["pi", "claude"]}),
            force=True)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertTrue(payload["tools"]["claude"]["available"])
        self.assertFalse(payload["tools"]["opencode"]["available"])
        self.assertEqual(len(payload["tools"]["pi"]["models"]), 3)

    def test_a_fresh_file_is_not_rewritten(self):
        run = self._run({"stdout": PI_OUTPUT, "_which": ["pi"]})
        self.assertIsNotNone(host_targets.refresh(self.root, run=run, force=True))
        # Ohne force bleibt eine frische Datei unangetastet.
        self.assertIsNone(host_targets.refresh(self.root, run=run))


if __name__ == "__main__":
    unittest.main()
