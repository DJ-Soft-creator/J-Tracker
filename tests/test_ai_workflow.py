import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT_DIR / "scripts" / "ai-einkaufsliste.py"


class ShoppingWorkflowTests(unittest.TestCase):
    def test_prepare_includes_request_and_configured_family_context(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            context_path = data_dir / "family" / "ai_context.json"
            context_path.parent.mkdir(parents=True)
            context_path.write_text(json.dumps({"family_size": 5, "preferences": ["vegetarisch"]}), encoding="utf-8")
            process = subprocess.run(
                [sys.executable, str(WORKFLOW), "prepare"],
                input=json.dumps({"user_id": "user-a", "text": "Schnelles Mittagessen #ai-einkaufsliste"}),
                text=True,
                capture_output=True,
                check=False,
                env={**os.environ, "DATA_DIR": str(data_dir)},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["workflow"], "ai-einkaufsliste")
            self.assertIn("family_size", result["opencode_prompt"])
            self.assertIn("Schnelles Mittagessen", result["opencode_prompt"])

    def test_apply_keeps_non_json_output_as_journal_response(self):
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.run(
                [sys.executable, str(WORKFLOW), "apply"],
                input=json.dumps({"user_id": "user-a", "opencode_result": "Bitte Milch kaufen."}),
                text=True,
                capture_output=True,
                check=False,
                env={**os.environ, "DATA_DIR": directory},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), {
                "journal_response": "Bitte Milch kaufen.",
                "changed_files": [],
            })
