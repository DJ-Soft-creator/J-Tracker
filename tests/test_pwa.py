import importlib.util
import json
import os
import struct
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT_DIR = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "pwa-test-secret")
    os.environ.setdefault("DATA_DIR", "/tmp/opencode/journl-pwa-tests")
    sys.path.insert(0, str(ROOT_DIR / "app"))
    import main


class _HeadTagParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.metas = []

    def handle_starttag(self, tag, attrs):
        attributes = {name.lower(): value for name, value in attrs}
        if tag.lower() == "link":
            self.links.append(attributes)
        elif tag.lower() == "meta":
            self.metas.append(attributes)


class PwaAssetTests(unittest.TestCase):
    def test_runtime_config_is_stored_in_the_data_directory(self):
        main_source = (ROOT_DIR / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('CONFIG_PATH = DATA_DIR / "config.json"', main_source)
        self.assertNotIn('BASE_DIR / "config.json"', main_source)

        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('id="brain-tag-provider"', template)
            self.assertIn(".filter(p => String(p.id || '').startsWith('lm_'))", template)
            self.assertIn("brainRunButton.disabled = localProviders.length === 0", template)
            self.assertNotIn('<option value="lm_studio">LM Studio</option>', template)

    def test_application_version_is_shipped_inside_the_app(self):
        shipped_version = (ROOT_DIR / "app" / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(shipped_version, r"^\d+\.\d+\.\d+$")
        self.assertFalse((ROOT_DIR / "VERSION").exists())
        main_source = (ROOT_DIR / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('VERSION_PATH = Path(__file__).with_name("VERSION")', main_source)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('id="settings-app-version">–</span>', template)
            self.assertNotIn('id="settings-app-version">2.1.1</span>', template)

    def test_brain_is_integrated_in_both_main_navigations(self):
        desktop = (ROOT_DIR / "app" / "templates" / "desktop.html").read_text(encoding="utf-8")
        mobile = (ROOT_DIR / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        brain_client = (ROOT_DIR / "app" / "static" / "brain.js").read_text(encoding="utf-8")

        self.assertIn('id="tab-btn-brain"', desktop)
        self.assertIn('id="tab-brain"', desktop)
        self.assertIn("filename='brain.js'", desktop)
        self.assertIn('id="tab-btn-brain" aria-label="Brain"', mobile)
        self.assertIn('id="tab-btn-write" aria-label="Schreiben"', mobile)
        self.assertIn('id="tab-btn-dashboard" aria-label="Heute"', mobile)
        self.assertIn('id="tab-btn-family" aria-label="Familie"', mobile)
        for template in (desktop, mobile):
            self.assertIn('id="brain-mode-notes"', template)
            self.assertIn('id="brain-mode-projects"', template)
            self.assertIn('id="brain-mode-family"', template)
            self.assertIn('id="brain-mode-journals"', template)
            self.assertIn('id="brain-range-start"', template)
            self.assertIn('id="brain-range-end"', template)
            self.assertIn('data-brain-date-picker', template)
            self.assertIn("logo-dark.png", template)
        self.assertIn("/api/brain/search", brain_client)
        self.assertIn("/api/brain/bootstrap", brain_client)
        self.assertIn("query.focus({ preventScroll: true })", brain_client)
        self.assertIn("data-ai-classification", brain_client)
        self.assertNotIn("data-tag-classification", brain_client)
        self.assertIn("Diverse Files", brain_client)
        self.assertIn("Komplettes Journal", brain_client)
        self.assertIn("Nur Blockeintrag", brain_client)
        self.assertIn("/api/brain/notes", brain_client)
        self.assertIn("/api/brain/projects", brain_client)
        self.assertIn("/api/brain/family", brain_client)
        self.assertIn("/api/brain/task/toggle", brain_client)
        self.assertIn("params.set('start_date', rangeStart)", brain_client)
        self.assertIn("mode === 'journals' ? 'journal' : 'all'", brain_client)
        self.assertIn("data-brain-inline-editor", brain_client)
        self.assertIn("data-brain-manage", brain_client)
        self.assertNotIn("brain-editor-back", brain_client)
        self.assertIn('id="brain-filter-toggle"', mobile)
        self.assertIn('aria-controls="brain-filter-panel"', mobile)
        self.assertIn('id="brain-filter-panel" class="hidden space-y-2"', mobile)
        self.assertNotIn('id="brain-filter-toggle"', desktop)
        for template in (desktop, mobile):
            self.assertNotIn('id="brain-editor"', template)
            self.assertIn('id="template-select" class="h-10', template)
            self.assertIn('id="submit-btn" onclick="handleSubmit()" class="h-10', template)
            self.assertIn('id="ai-controls" class="hidden flex flex-nowrap', template)

    def test_mobile_navigation_is_below_content_and_header_is_compact(self):
        mobile = (ROOT_DIR / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        desktop = (ROOT_DIR / "app" / "templates" / "desktop.html").read_text(encoding="utf-8")

        self.assertGreater(mobile.index('id="mobile-tab-bar"'), mobile.index("</main>"))
        self.assertIn('id="app-header" class="{% if IS_DEV %}bg-red-900{% else %}bg-gray-900{% endif %} px-4 py-1', mobile)
        self.assertIn('alt="Journl" class="h-7 w-7 object-contain"', mobile)
        self.assertIn("#app > header {\n      padding-top: 0.25rem;", mobile)
        self.assertNotIn("#app > header {\n      padding-top: calc(0.25rem + var(--safe-top));", mobile)
        self.assertIn("padding-bottom: min(var(--safe-bottom), 0.75rem);", mobile)
        self.assertNotIn('id="mobile-tab-bar"', desktop)

    def test_private_ai_draft_is_scoped_to_ai_mode(self):
        write_ai = (ROOT_DIR / "app" / "static" / "write-ai.js").read_text(encoding="utf-8")

        self.assertIn("editorMode !== 'ai' || !isWritingMode()", write_ai)
        self.assertIn("window.writeAiDraftRevisionForSubmit = async function", write_ai)
        self.assertIn("/commit-draft", write_ai)
        self.assertIn("mergeResponseWithNewInput", write_ai)
        self.assertIn("current.startsWith(snapshot)", write_ai)
        self.assertIn("function setEditorContent(content,", write_ai)
        self.assertIn("field.dispatchEvent(new Event('input', { bubbles: true }))", write_ai)
        self.assertNotIn("`${responseText}\\n\\n${field.value}`", write_ai)
        write_hashtags = (ROOT_DIR / "app" / "static" / "write-hashtags.js").read_text(encoding="utf-8")
        self.assertIn("state.content.innerHTML = highlightMarkup(input.value);", write_hashtags)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertNotIn(".write-hashtag-caret", template)

    def test_normal_submit_only_clears_the_text_that_was_sent(self):
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn("let submittedSimpleValue = null;", template)
            self.assertIn("textarea.value === submittedSimpleValue", template)
            self.assertIn("const submittedFormValues = new Map();", template)
            self.assertIn("Gespeichert – neuer Text bleibt im Eingabefeld.", template)
            self.assertIn("const normalSubmissionClearKey = 'journl:clear-submitted-write-input';", template)
            self.assertIn("rememberSuccessfulNormalSubmission();", template)
            self.assertIn("sessionStorage.removeItem(normalSubmissionClearKey)", template)
            self.assertIn("payload.consume_draft_revision = draftRevisionToConsume", template)
            self.assertIn("window.markWriteAiDraftSubmitted(responseData)", template)

    def test_new_page_starts_blank_and_only_explicitly_resumes_server_session(self):
        write_session = (ROOT_DIR / "app" / "static" / "write-session.js").read_text(encoding="utf-8")
        brain_client = (ROOT_DIR / "app" / "static" / "brain.js").read_text(encoding="utf-8")
        self.assertIn("resetEditor('');", write_session)
        self.assertIn("● 0", (ROOT_DIR / "app" / "templates" / "desktop.html").read_text(encoding="utf-8"))
        self.assertIn("● 0", (ROOT_DIR / "app" / "templates" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("jsonRequest('/api/write-sessions'", write_session)
        self.assertIn("window.writeSessionForSubmit", write_session)
        self.assertIn("navigator.wakeLock.request('screen')", write_session)
        self.assertIn("recorder.start(10000)", write_session)
        self.assertNotIn("localStorage", write_session)
        self.assertNotIn("alert(", write_session)
        self.assertIn("confirm(", write_session)
        write_ai = (ROOT_DIR / "app" / "static" / "write-ai.js").read_text(encoding="utf-8")
        self.assertIn("restoreCurrentSession ? (data.content || '') : ''", write_ai)
        self.assertIn("window.resumeWriteAiSession = function", write_ai)
        journal_highlighting = (ROOT_DIR / "app" / "static" / "journal-highlighting.js").read_text(encoding="utf-8")
        self.assertIn("jt:(?:media|transcript)", journal_highlighting)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn("payload.write_session_id = writeSessionId", template)
            self.assertIn("window.markWriteSessionSubmitted(responseData)", template)
            self.assertIn("filename='write-history.js', v='20260810-1'", template)
            self.assertIn("filename='write-ai.js', v='20260810-3'", template)
            self.assertIn("filename='write-session.js', v='20260810-8'", template)
            self.assertIn("filename='journal-highlighting.js', v='20260810-3'", template)
            self.assertIn("filename='brain.js', v='20260810-14'", template)
            self.assertIn('id="settings-transcribe-now"', template)
        self.assertIn("document.getElementById('input-area').insertAdjacentElement('afterend', strip)", write_session)
        self.assertIn("Foto angehängt", write_session)
        self.assertIn("Wird beim Senden übernommen", write_session)
        self.assertIn("sessionButton.classList.remove('text-green-400')", write_session)
        self.assertIn("Dokument anhängen", write_session)
        self.assertIn("data-remove-media", write_session)
        self.assertIn("chunk_count", write_session)
        self.assertIn("window.renderJournalMedia(item.media)", brain_client)
        self.assertIn('class="text-gray-500"', journal_highlighting)

    def test_whisper_initialization_and_operations_are_documented(self):
        init_script = (ROOT_DIR / "scripts" / "init-whisper.sh").read_text(encoding="utf-8")
        operations = (ROOT_DIR / "scripts" / "README_whisper.md").read_text(encoding="utf-8")
        whisper_app = (ROOT_DIR / "whisper_service" / "app.py").read_text(encoding="utf-8")

        for key in (
            "WHISPER_API_KEY", "WHISPER_MODEL", "WHISPER_COMPUTE_TYPE",
            "WHISPER_CPU_THREADS", "WHISPER_PORT", "WHISPER_SCHEDULE_HOUR",
            "WHISPER_LANGUAGE",
        ):
            self.assertIn(key, init_script)
        self.assertIn("openssl rand -hex 32", init_script)
        self.assertIn("/v1/models/load", init_script)
        self.assertIn('expected_root="/docker-storage/Journal-Tracker-DEV"', init_script)
        self.assertIn('expected_root="/docker-storage/Journal-Tracker"', init_script)
        self.assertIn("sudo ./scripts/init-whisper.sh dev", operations)
        self.assertIn("sudo ./scripts/init-whisper.sh prod", operations)
        self.assertIn('@app.post("/v1/models/load")', whisper_app)

    def test_manifest_has_expected_metadata(self):
        manifest_path = ROOT_DIR / "app" / "static" / "manifest.webmanifest"
        self.assertEqual(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            {
                "id": "/",
                "start_url": "/",
                "scope": "/",
                "name": "Journl",
                "short_name": "Journl",
                "lang": "de",
                "display": "standalone",
                "theme_color": "#030712",
                "background_color": "#030712",
                "icons": [
                    {
                        "src": "/static/icon-192.png",
                        "sizes": "192x192",
                        "type": "image/png",
                        "purpose": "any",
                    },
                    {
                        "src": "/static/icon-512.png",
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "any",
                    },
                ],
            },
        )

    def test_icons_are_opaque_eight_bit_pngs_with_exact_dimensions(self):
        expected_icons = {
            "apple-touch-icon.png": (180, 180),
            "icon-192.png": (192, 192),
            "icon-512.png": (512, 512),
        }

        for filename, expected_dimensions in expected_icons.items():
            with self.subTest(filename=filename):
                data = (ROOT_DIR / "app" / "static" / filename).read_bytes()
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")

                ihdr_length, chunk_type = struct.unpack(">I4s", data[8:16])
                self.assertEqual((ihdr_length, chunk_type), (13, b"IHDR"))
                width, height, bit_depth, color_type = struct.unpack(
                    ">IIBB", data[16:26]
                )
                self.assertEqual((width, height), expected_dimensions)
                self.assertEqual(bit_depth, 8)
                self.assertEqual(
                    color_type, 2, "flattened icons must be opaque RGB PNGs"
                )


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class PwaFlaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)

    def setUp(self):
        self.client = main.app.test_client()

    def test_api_config_overrides_persisted_version_with_app_version(self):
        with self.client.session_transaction() as session:
            session["user_id"] = "version-user"
        with mock.patch.object(main, "load_config", return_value={"app_version": "from-data"}), \
                mock.patch.object(main, "load_app_version", return_value="2.3.1"), \
                mock.patch.object(main, "_current_user", return_value={
                    "id": "version-user", "username": "Version", "admin": False,
                }), \
                mock.patch.object(main, "_read_users_file", return_value={"users": []}):
            response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["app_version"], "2.3.1")

    def test_static_assets_are_served_with_expected_mime_types(self):
        expected_assets = {
            "/static/manifest.webmanifest": "application/manifest+json",
            "/static/apple-touch-icon.png": "image/png",
            "/static/icon-192.png": "image/png",
            "/static/icon-512.png": "image/png",
        }

        for url, expected_mimetype in expected_assets.items():
            with self.subTest(url=url):
                with self.client.get(url) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.mimetype, expected_mimetype)

    def test_both_rendered_templates_include_pwa_head_tags(self):
        user_agents = {
            "mobile": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
            ),
            "desktop": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        }

        for device, user_agent in user_agents.items():
            with self.subTest(device=device):
                response = self.client.get("/", headers={"User-Agent": user_agent})
                self.assertEqual(response.status_code, 200)

                parser = _HeadTagParser()
                parser.feed(response.get_data(as_text=True))
                links_by_rel = {
                    rel.lower(): link.get("href", "")
                    for link in parser.links
                    for rel in (link.get("rel") or "").split()
                }
                self.assertEqual(
                    urlsplit(links_by_rel.get("manifest", "")).path,
                    "/static/manifest.webmanifest",
                )
                self.assertEqual(
                    urlsplit(links_by_rel.get("apple-touch-icon", "")).path,
                    "/static/apple-touch-icon.png",
                )

                metas_by_name = {
                    meta.get("name", "").lower(): (meta.get("content") or "").lower()
                    for meta in parser.metas
                }
                self.assertEqual(
                    metas_by_name.get("apple-mobile-web-app-capable"), "yes"
                )
                self.assertIn(
                    metas_by_name.get("apple-mobile-web-app-status-bar-style"),
                    {"black", "black-translucent"},
                )

    def test_user_agents_select_the_correct_template(self):
        cases = {
            "iPhone": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
                "index.html",
            ),
            "iPad": (
                "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) "
                "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
                "index.html",
            ),
            "iPadOS desktop mode": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
                "Mobile/15E148 Safari/604.1",
                "index.html",
            ),
            "iPod": (
                "Mozilla/5.0 (iPod touch; CPU iPhone OS 15_7 like Mac OS X) "
                "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
                "index.html",
            ),
            "Windows Phone": (
                "Mozilla/5.0 (Windows Phone 10.0; Android 6.0.1; Microsoft; "
                "Lumia 950 XL Dual SIM) AppleWebKit/537.36 Mobile Safari/537.36",
                "index.html",
            ),
            "Android mobile": (
                "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) "
                "AppleWebKit/537.36 Chrome/126.0.0.0 Mobile Safari/537.36",
                "index.html",
            ),
            "Android tablet": (
                "Mozilla/5.0 (Linux; Android 14; Pixel Tablet) "
                "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                "desktop.html",
            ),
            "Mac desktop": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
                "Safari/605.1.15",
                "desktop.html",
            ),
            "desktop": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/126.0.0.0 Safari/537.36",
                "desktop.html",
            ),
        }

        for device, (user_agent, expected_template) in cases.items():
            with self.subTest(device=device):
                with mock.patch.object(
                    main, "render_template", return_value="rendered"
                ) as render_template:
                    response = self.client.get(
                        "/", headers={"User-Agent": user_agent}
                    )

                self.assertEqual(response.status_code, 200)
                render_template.assert_called_once_with(expected_template)


if __name__ == "__main__":
    unittest.main()
