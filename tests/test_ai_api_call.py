"""Provider-Aufruf ``_call_ai_api``: Zeitlimits, Bilder, Reasoning, Streaming."""

import hashlib
import importlib.util
import json
from unittest import mock

try:
    from .brain_api_case import BrainApiCase
except ImportError:  # unittest discovery adds tests/ directly to sys.path
    from brain_api_case import BrainApiCase

if importlib.util.find_spec("flask") is not None:
    import main


class AiApiCallTests(BrainApiCase):
    def test_ai_api_allows_five_minutes_for_response(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "classified"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener):
            result = main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                {
                    "max_tokens": 1234,
                    "temperature": 0.1,
                    "response_format": {"type": "json_schema"},
                },
                "journal",
            )

        self.assertEqual(result, "classified")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 300)
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload["max_tokens"], 1234)
        self.assertEqual(payload["temperature"], 0.1)
        self.assertEqual(payload["response_format"], {"type": "json_schema"})

    def _payload_for(self, provider):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response
        with mock.patch("urllib.request.build_opener", return_value=opener):
            main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test",
                 **provider},
                {}, "journal",
            )
        return json.loads(opener.open.call_args.args[0].data)

    def test_thinking_budget_is_sent_only_when_configured(self):
        self.assertEqual(self._payload_for({"thinking_budget": 4000})["thinking_budget_tokens"], 4000)
        self.assertNotIn("thinking_budget_tokens", self._payload_for({}))

    def test_ai_api_honours_a_longer_caller_timeout(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "fertig"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener):
            main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                {"timeout": 1800},
                "grosser analyselauf",
            )

        self.assertEqual(opener.open.call_args.kwargs["timeout"], 1800)

    def test_ai_api_clamps_an_absurd_timeout(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener):
            main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                {"timeout": 999999},
                "zu lang",
            )

        self.assertEqual(opener.open.call_args.kwargs["timeout"], 7200)

    def test_ai_api_sends_hash_checked_image_as_openai_content_block(self):
        image = self.data_dir / "user-a/ai_jobs/attachments/11111111-1111-1111-1111-111111111111/image-1.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"\x89PNG\r\n\x1a\nprivate-image")
        attachment = {
            "type": "image", "mime_type": "image/png",
            "path": image.relative_to(self.data_dir).as_posix(),
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "byte_size": image.stat().st_size,
        }
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "described"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener), \
             mock.patch.object(main._main_ai, "DATA_DIR", self.data_dir):
            result = main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                {}, "describe", image_attachments=[attachment],
            )

        self.assertEqual(result, "described")
        payload = json.loads(opener.open.call_args.args[0].data)
        content = payload["messages"][-1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "describe"})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_ai_api_rejects_reasoning_only_for_required_content(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "", "reasoning_content": "Still thinking"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "required structured output"):
                main._call_ai_api(
                    {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                    {"require_content": True},
                    "journal",
                )

    def test_ai_api_names_the_token_limit_when_thinking_used_it_up(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"finish_reason": "length",
                         "message": {"content": "", "reasoning_content": "Hmm, erst einmal ..."}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, r"Token-Limit \(4000\) aufgebraucht"):
                main._call_ai_api(
                    {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                    {"require_content": True, "max_tokens": 4000},
                    "journal",
                )

    def test_ai_api_keeps_reasoning_fallback_for_other_functions(self):
        response = mock.MagicMock()
        response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "", "reasoning_content": "Useful answer"}}]
        }).encode("utf-8")
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response

        with mock.patch("urllib.request.build_opener", return_value=opener):
            result = main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                {},
                "journal",
            )

        self.assertEqual(result, "Useful answer")

    def _stream_opener(self, lines):
        response = mock.MagicMock()
        response.readline.side_effect = list(lines) + [b""]
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response
        return opener

    def test_ai_api_streams_and_joins_the_deltas(self):
        """Ein langer Lauf streamt: der Timeout wirkt dann nur als Leerlaufgrenze."""
        def event(delta):
            return ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n").encode()
        opener = self._stream_opener([
            b": keep-alive\n", event({"reasoning_content": "denke"}),
            event({"content": "Hallo "}), b"\n", event({"content": "Welt"}), b"data: [DONE]\n",
        ])
        with mock.patch("urllib.request.build_opener", return_value=opener):
            result = main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                {"stream": True, "timeout": 900, "require_content": True}, "journal",
            )
        self.assertEqual(result, "Hallo Welt")
        self.assertTrue(json.loads(opener.open.call_args.args[0].data)["stream"])
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 900)

    def test_ai_api_accepts_plain_json_when_the_provider_ignores_stream(self):
        body = json.dumps({"choices": [{"message": {"content": "ganz"}}]}).encode()
        response = mock.MagicMock()
        response.readline.return_value = body
        response.read.return_value = b""
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response
        with mock.patch("urllib.request.build_opener", return_value=opener):
            result = main._call_ai_api(
                {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                {"stream": True}, "journal",
            )
        self.assertEqual(result, "ganz")

    def test_ai_api_reports_an_error_event_in_the_stream(self):
        opener = self._stream_opener([b'data: {"error": {"message": "context too long"}}\n'])
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ConnectionError, "context too long"):
                main._call_ai_api(
                    {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                    {"stream": True}, "journal",
                )

    def test_ai_api_names_a_stalled_stream(self):
        response = mock.MagicMock()
        response.readline.side_effect = [b'data: {"choices": [{"delta": {"content": "a"}}]}\n', TimeoutError()]
        response.__enter__.return_value = response
        opener = mock.MagicMock()
        opener.open.return_value = response
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ConnectionError, "no data for 900 seconds"):
                main._call_ai_api(
                    {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                    {"stream": True, "timeout": 900}, "journal",
                )

    def test_ai_api_stops_a_stream_at_the_total_limit(self):
        opener = self._stream_opener([b'data: {"choices": [{"delta": {"content": "a"}}]}\n'] * 3)
        ticks = [0]

        def clock():
            ticks[0] += 50
            return ticks[0]
        with mock.patch("urllib.request.build_opener", return_value=opener), \
             mock.patch.object(main._main_ai.time, "monotonic", side_effect=clock):
            with self.assertRaisesRegex(ConnectionError, "exceeded 60 seconds"):
                main._call_ai_api(
                    {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                    {"stream": True, "max_duration": 60}, "journal",
                )

    def test_ai_api_stops_a_runaway_stream_that_ignores_max_tokens(self):
        """Ein Endpunkt, der max_tokens ignoriert, darf nicht bis zur Gesamtgrenze laufen."""
        chunk = b'data: {"choices": [{"delta": {"reasoning_content": "' + b"x" * 1000 + b'"}}]}\n'
        opener = self._stream_opener([chunk] * 30)
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ConnectionError, "max_tokens offenbar nicht"):
                main._call_ai_api(
                    {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                    {"stream": True, "max_tokens": 100, "max_stream_chars": 20000}, "journal",
                )

    def test_ai_api_stream_honours_a_cancel_request(self):
        opener = self._stream_opener([b'data: {"choices": [{"delta": {"content": "a"}}]}\n'] * 5)
        ticks = [0]

        def clock():
            ticks[0] += 10
            return ticks[0]
        with mock.patch("urllib.request.build_opener", return_value=opener), \
             mock.patch.object(main._main_ai.time, "monotonic", side_effect=clock):
            with self.assertRaisesRegex(ConnectionError, "abgebrochen"):
                main._call_ai_api(
                    {"id": "test", "api_url": "http://localhost/v1/chat/completions", "model": "test"},
                    {"stream": True, "should_stop": lambda: True}, "journal",
                )
