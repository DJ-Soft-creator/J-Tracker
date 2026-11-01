import gzip
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

APP_SHELL_ASSETS = {
    "index.html": ("mobile-app-core.js", "mobile-session.js", "mobile-app-features.js"),
    "desktop.html": ("desktop-app-core.js", "desktop-app-features.js", "desktop-app-resize.js"),
}


def _template_behavior_source(template_name):
    """Return behavior owned by the ordered classic scripts for one shell."""
    return "\n".join(
        (ROOT_DIR / "app" / "static" / asset).read_text(encoding="utf-8")
        for asset in APP_SHELL_ASSETS[template_name]
    )


def _shell_style_source(template_name):
    """Template markup plus the stylesheet it links, for CSS assertions.

    The mobile shell CSS lives in its own file; the desktop shell is still
    inline. Both are checked through this one source so the assertions do not
    have to know where a rule happens to sit.
    """
    text = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
    stylesheet = ROOT_DIR / "app" / "static" / "mobile-shell.css"
    if template_name == "index.html":
        text += "\n" + stylesheet.read_text(encoding="utf-8")
    return text


def _brain_behavior_source():
    """Return the ordered Brain client source used by both app shells."""
    return "\n".join(
        (ROOT_DIR / "app" / "static" / asset).read_text(encoding="utf-8")
        for asset in ("brain.js", "brain-cards.js", "brain-tag-filters.js", "brain-select.js", "brain-content.js")
    )

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

        job = (ROOT_DIR / "app" / "static" / "journal-analysis-tagging.js").read_text(encoding="utf-8")
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('id="brain-tag-connection"', template)
            self.assertIn('id="brain-tag-manage"', template)
            self.assertIn("filename='journal-analysis-tagging.js'", template)
            self.assertNotIn('id="brain-tag-provider"', template)
            self.assertNotIn('<option value="lm_studio">LM Studio</option>', template)
        for endpoint in ("settings", "models", "job/run"):
            self.assertIn("/api/brain/tagging/" + endpoint, job)

    def test_every_tab_panel_contains_its_own_content(self):
        """Ein falsch geschlossenes <div> haengt Inhalt aus seinem Tab aus.

        ``switchTab`` versteckt genau die ``.tab-content``-Container. Rutscht
        ein Panel darunter heraus, bleibt es beim Tabwechsel stehen — sichtbar
        nur im Browser, unsichtbar fuer jede Textzusicherung. Genau so standen
        die Brain-Treffer nach einem Wechsel weiter unten auf dem Schirm.
        """
        void_elements = {
            "br", "img", "input", "meta", "link", "hr", "source", "area",
            "base", "col", "embed", "param", "track", "wbr",
        }

        class Nesting(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.parents = {}

            def handle_starttag(self, tag, attrs):
                if tag in void_elements:
                    return
                identifier = dict(attrs).get("id")
                self.stack.append((tag, identifier))
                if identifier:
                    self.parents[identifier] = [name for _, name in self.stack if name]

            def handle_endtag(self, tag):
                for index in range(len(self.stack) - 1, -1, -1):
                    if self.stack[index][0] == tag:
                        del self.stack[index:]
                        return

        expected = {
            "brain-browser": "tab-brain",
            "brain-app": "tab-brain",
            "brain-tag-filter-area": "tab-brain",
            "dashboard-container": "tab-dashboard",
            "family-container": "tab-family",
            "input-area": "tab-write",
        }
        for template_name in ("desktop.html", "index.html"):
            parser = Nesting()
            parser.feed((ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8"))
            for element, tab in expected.items():
                chain = parser.parents.get(element)
                self.assertIsNotNone(chain, f"{element} fehlt in {template_name}")
                self.assertIn(tab, chain, f"{element} haengt in {template_name} ausserhalb von {tab}: {chain}")

    def test_journal_db_mode_is_gated_and_brain_actions_sit_in_one_menu(self):
        """Der DB-Modus haengt am selben Flag wie sein Endpunkt.

        Der Knopf "Journal DB" erscheint nur, wenn der Suchindex aktiv ist,
        sonst beantwortete er jeden Klick mit "Journalsuche ist nicht aktiv".
        """
        main_source = (ROOT_DIR / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"DB_SEARCH_ENABLED": is_enabled()', main_source)
        routes = (ROOT_DIR / "app" / "main_journal_routes.py").read_text(encoding="utf-8")
        self.assertIn("if not is_enabled():", routes)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            start = template.index("{% if DB_SEARCH_ENABLED %}")
            end = template.index("{% endif %}", start)
            self.assertIn('id="brain-mode-db-journals"', template[start:end])
            # Aktionen gehoeren ins Brain-Menue, nicht in die Moduszeile.
            self.assertIn("data-action-menu-trigger", template)
            self.assertIn('id="brain-menu-catalog"', template)
            self.assertIn('id="brain-menu-select"', template)
            self.assertIn('id="brain-menu-copy"', template)
            self.assertIn('id="brain-menu-download"', template)
            self.assertGreater(
                template.index("data-brain-hashtag-replace"),
                template.index("data-action-menu-items"),
            )
        brain_content = (ROOT_DIR / "app" / "static" / "brain-content.js").read_text(encoding="utf-8")
        self.assertIn("brainElement('brain-mode-db-journals')?.addEventListener", brain_content)

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
        mobile = _shell_style_source("index.html")
        brain_client = _brain_behavior_source()

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
            self.assertIn('id="brain-mode-ai-agent"', template)
            self.assertIn('id="brain-mode-agent-audit"', template)
            self.assertIn('id="brain-mode-family"', template)
            self.assertIn('id="brain-mode-journals"', template)
            self.assertIn('id="brain-range-start"', template)
            self.assertIn('id="brain-range-end"', template)
            self.assertIn('type="date" class="sr-only" tabindex="-1" aria-hidden="true"', template)
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
        self.assertIn("/api/brain/ai-agent", brain_client)
        self.assertIn("/api/ai-sessions/audit", brain_client)
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
        # Desktop can collapse the same panel, but starts expanded.
        self.assertIn('id="brain-filter-toggle"', desktop)
        self.assertIn('aria-controls="brain-filter-panel"', desktop)
        self.assertIn('id="brain-filter-panel" class="space-y-2"', desktop)
        for template in (desktop, mobile):
            self.assertNotIn('id="brain-editor"', template)
            self.assertIn('id="template-select" class="h-10', template)
            self.assertIn('id="submit-btn" onclick="handleSubmit()" class="h-10', template)
            self.assertIn('id="ai-controls" class="hidden flex flex-nowrap', template)

    def test_mobile_navigation_is_below_content_and_header_is_compact(self):
        mobile = _shell_style_source("index.html")
        desktop = (ROOT_DIR / "app" / "templates" / "desktop.html").read_text(encoding="utf-8")

        self.assertGreater(mobile.index('id="mobile-tab-bar"'), mobile.index("</main>"))
        self.assertIn('id="app-header" class="{% if IS_DEV %}bg-red-900{% else %}bg-gray-900{% endif %} px-4 py-1', mobile)
        self.assertIn('alt="Journl" class="h-7 w-7 object-contain"', mobile)
        # Fullscreen-App: der Kopf muss Statusleiste bzw. Notch freihalten,
        # bleibt darunter aber kompakt.
        self.assertIn("#app > header {\n  padding-top: calc(max(1.5rem, var(--safe-top)) + 0.25rem);", mobile)
        self.assertNotIn("#app > header {\n  padding-top: calc(0.25rem + var(--safe-top));", mobile)
        self.assertIn("padding-bottom: max(var(--safe-bottom), 0.25rem);", mobile)
        self.assertIn('maximum-scale=1.0, user-scalable=no', mobile)
        self.assertIn('id="write-primary-actions"', mobile)
        self.assertIn("filename='mobile-pwa-gestures.js', v='20260815-1'", mobile)
        gestures = (ROOT_DIR / "app" / "static" / "mobile-pwa-gestures.js").read_text(encoding="utf-8")
        self.assertIn("'(display-mode: standalone)'", gestures)
        self.assertIn("event.touches.length > 1", gestures)
        self.assertIn("edgeWidth = 28", gestures)
        self.assertIn("{ passive: false }", gestures)
        self.assertNotIn('id="mobile-tab-bar"', desktop)

    def test_private_ai_draft_is_scoped_to_ai_mode(self):
        write_ai = (ROOT_DIR / "app" / "static" / "write-ai.js").read_text(encoding="utf-8")
        self.assertIn("window.writeSessionForSubmit?.(snapshot)", write_ai)
        self.assertIn("payload.write_session_id = writeSessionId", write_ai)
        template_hashtags = (ROOT_DIR / "app" / "static" / "write-template-hashtags.js").read_text(encoding="utf-8")

        self.assertIn("editorMode !== 'ai' || !isWritingMode()", write_ai)
        self.assertIn("window.writeAiDraftRevisionForSubmit = async function", write_ai)
        self.assertIn("/commit-draft", write_ai)
        self.assertIn("mergeResponseWithNewInput", write_ai)
        self.assertIn("current.startsWith(snapshot)", write_ai)
        self.assertIn("function setEditorContent(content,", write_ai)
        self.assertIn("field.dispatchEvent(new Event('input', { bubbles: true }))", write_ai)
        self.assertNotIn("`${responseText}\\n\\n${field.value}`", write_ai)
        self.assertIn("await window.prepareTemplateHashtagsForAiMode()", write_ai)
        self.assertIn("template.hashtag_template !== true", template_hashtags)
        self.assertIn("const body = stripManagedTags(field.value);", template_hashtags)
        self.assertIn("Hashtags übernehmen", template_hashtags)
        self.assertIn("Ohne Hashtags", template_hashtags)
        self.assertIn("data-choice=\"cancel\"", template_hashtags)
        write_hashtags = (ROOT_DIR / "app" / "static" / "write-hashtags.js").read_text(encoding="utf-8")
        # Der Overlay-Caret gehoert genau dorthin, wo die deckende Hashtag-Maske
        # den nativen Caret verschluckt. Im Fliesstext gezeichnet stuende dort
        # ein zweiter, um Pixel versetzter Cursor - deshalb genau eine Stelle.
        self.assertIn("state.content.innerHTML = highlightMarkup(input.value, state.caret);", write_hashtags)
        self.assertIn("function maskedCaret(input) {", write_hashtags)
        self.assertEqual(write_hashtags.count('class="write-hashtag-caret"'), 1)
        for template_name in ("desktop.html", "index.html"):
            style = _shell_style_source(template_name)
            self.assertIn(".write-hashtag-caret::before", style)

    def test_hashtag_editor_uses_native_text_and_viewport_aware_picker(self):
        write_hashtags = (ROOT_DIR / "app" / "static" / "write-hashtags.js").read_text(encoding="utf-8")

        self.assertIn("verticalScrollbarWidth(state.input, style)", write_hashtags)
        self.assertIn("marker.textContent = '\\u200b';", write_hashtags)
        self.assertNotIn("marker.textContent = input.value.slice(cursor)", write_hashtags)
        self.assertIn("const viewport = window.visualViewport;", write_hashtags)
        self.assertIn("getPropertyValue('--safe-bottom')", write_hashtags)
        self.assertIn("const placeBelow = belowSpace >= desiredHeight", write_hashtags)
        self.assertIn("MIN_COMFORTABLE_MENU_HEIGHT", write_hashtags)
        self.assertIn("event.isComposing || event.keyCode === 229", write_hashtags)
        self.assertIn("wrapper.classList.add('is-composing')", write_hashtags)
        self.assertIn("suggestion.status === 'write_target'", write_hashtags)
        self.assertIn(".forEach(syncInputHighlight)", write_hashtags)
        self.assertIn("aria-activedescendant", write_hashtags)
        self.assertIn("const MAX_SUGGESTIONS = 40;", write_hashtags)
        self.assertIn("setSelectedIndex(state, state.selectedIndex + direction)", write_hashtags)
        self.assertIn("event.target instanceof Node && menu.contains(event.target)", write_hashtags)
        self.assertIn("!menu.contains(document.activeElement)", write_hashtags)

        for template_name in ("desktop.html", "index.html"):
            template = _shell_style_source(template_name)
            self.assertIn("color: transparent; background: transparent;", template)
            self.assertIn("color: #f3f4f6 !important;", template)
            self.assertNotIn("color: transparent !important", template)
            self.assertIn(".write-hashtag-editor.has-selection .write-hashtag-highlight", template)
            self.assertIn(".write-hashtag-unrecognised { color: #d1d5db;", template)
            self.assertIn("filename='write-hashtags.js', v='20260829-1'", template)

    def test_deep_write_mode_dims_only_the_chrome_around_the_editor(self):
        """Der Ruhe-Modus ist reine Darstellung und laesst den Text in Ruhe."""
        module = (ROOT_DIR / "app" / "static" / "write-deep-mode.js").read_text(encoding="utf-8")
        self.assertIn("const STORAGE_KEY = 'journl.write.deep-mode';", module)
        # Die Zeile haengt im geteilten "..."-Menue, das write-session.js besitzt.
        self.assertIn("document.getElementById('write-media-menu')", module)
        self.assertIn("fontSection.insertAdjacentElement('afterend', toggle)", module)
        self.assertIn("classList.toggle('deep-write', enabled)", module)
        self.assertIn("Deep Write Mode", module)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn("filename='write-deep-mode.js', v='20260829-1'", template)
            style = _shell_style_source(template_name)
            self.assertIn(".deep-write :is(header, nav,", style)
            # Editor und Eingabeflaeche bleiben unberuehrt - genau darum geht es.
            self.assertNotIn(".deep-write #simple-input", style)
            self.assertNotIn(".deep-write #input-surface", style)

    def test_normal_submit_only_clears_the_text_that_was_sent(self):
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            behavior = _template_behavior_source(template_name)
            self.assertIn("let submittedSimpleValue = null;", behavior)
            self.assertIn("textarea.value === submittedSimpleValue", behavior)
            self.assertIn("const submittedFormValues = new Map();", behavior)
            self.assertIn("Gespeichert – neuer Text bleibt im Eingabefeld.", behavior)
            self.assertIn("const normalSubmissionClearKey = 'journl:clear-submitted-write-input';", behavior)
            self.assertIn("rememberSuccessfulNormalSubmission();", behavior)
            self.assertIn("sessionStorage.removeItem(normalSubmissionClearKey)", behavior)
            self.assertIn("if (!submittedInput.value) return;", behavior)
            self.assertIn("el.dispatchEvent(new Event('input', { bubbles: true }))", behavior)
            self.assertIn("payload.consume_draft_revision = draftRevisionToConsume", behavior)
            self.assertIn("window.markWriteAiDraftSubmitted(responseData)", behavior)
            core_asset = "desktop-app-core.js" if template_name == "desktop.html" else "mobile-app-core.js"
            core_version = "20261004-1" if template_name == "desktop.html" else "20261007-1"
            self.assertIn(f"filename='{core_asset}', v='{core_version}'", template)

    def test_write_submit_button_recovers_without_reloading_the_app(self):
        submit_state = (ROOT_DIR / "app" / "static" / "write-submit-state.js").read_text(encoding="utf-8")
        write_ai = (ROOT_DIR / "app" / "static" / "write-ai.js").read_text(encoding="utf-8")
        source_edit = (ROOT_DIR / "app" / "static" / "write-source-edit.js").read_text(encoding="utf-8")

        self.assertIn("const lockMs = Number(options.lockMs) || 1000", submit_state)
        self.assertIn("entry.watchdogTimer = setTimeout", submit_state)
        self.assertIn("submit.disabled = Boolean(recordingReason)", submit_state)
        self.assertIn("supersedeScopes.includes(active.scope)", submit_state)
        self.assertNotIn("button.disabled = busy", write_ai)
        self.assertNotIn("submitButton.disabled = true", source_edit)
        for template_name, core_asset in (("index.html", "mobile-app-core.js"), ("desktop.html", "desktop-app-core.js")):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            behavior = (ROOT_DIR / "app" / "static" / core_asset).read_text(encoding="utf-8")
            self.assertLess(template.index("filename='write-submit-state.js'"), template.index(f"filename='{core_asset}'"))
            self.assertIn("await apiFetch('/api/dashboard/init', { method: 'POST', timeoutMs: 30000 })", behavior)
            self.assertNotIn("normalSubmissionInFlight", behavior)
            self.assertIn("supersedeScopes: ['ai']", behavior)

    def test_new_page_starts_blank_and_only_explicitly_resumes_server_session(self):
        write_session = (ROOT_DIR / "app" / "static" / "write-session.js").read_text(encoding="utf-8")
        brain_client = _brain_behavior_source()
        media_strip = (ROOT_DIR / "app" / "static" / "write-media-strip.js").read_text(encoding="utf-8")
        self.assertIn("resetEditor('');", write_session)
        self.assertIn("● 0", (ROOT_DIR / "app" / "templates" / "desktop.html").read_text(encoding="utf-8"))
        self.assertIn("● 0", (ROOT_DIR / "app" / "templates" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("jsonRequest('/api/write-sessions'", write_session)
        self.assertIn("window.writeSessionForSubmit", write_session)
        self.assertIn("navigator.wakeLock.request('screen')", write_session)
        self.assertIn("recorder.start(10000)", write_session)
        self.assertIn("recordingButton.classList.toggle('is-recording', recording)", write_session)
        self.assertIn("Sprachnachricht stoppen", write_session)
        self.assertIn("sessionActions.append(recordingWrap, mediaButton)", write_session)
        self.assertIn("recordingButton.addEventListener('click', toggleRecording)", write_session)
        self.assertIn("data-delete-all", write_session)
        self.assertIn("method: 'DELETE'", write_session)
        self.assertIn("auf allen Geräten gelöscht", write_session)
        self.assertIn("error.status === 404 && active", write_session)
        self.assertIn("discard_if_empty: true", write_session)
        self.assertIn("timeoutMs: 15000", write_session)
        self.assertIn("timeoutMs: 30000", write_session)
        self.assertIn("deine Eingabe wurde als neue Session gesichert", write_session)
        self.assertNotIn("write-undo-button", write_session)
        self.assertNotIn("const undoButton", write_session)
        self.assertIn('data-undo', write_session)
        # Das Markup des "..."-Menues gehoert write-media-menu.js, die Wirkung
        # der Zeilen bleibt hier.
        write_media_menu = (ROOT_DIR / "app" / "static" / "write-media-menu.js").read_text(encoding="utf-8")
        self.assertIn('data-undo', write_media_menu)
        self.assertIn('data-font-step="-1"', write_media_menu)
        self.assertIn('data-font-step="1"', write_media_menu)
        self.assertIn('title="Schrift verkleinern"', write_media_menu)
        self.assertIn('title="Schrift vergrößern"', write_media_menu)
        self.assertIn("field.style.setProperty('font-size', `${selected}px`, 'important')", write_session)
        self.assertIn("editorFontSize + Number(button.dataset.fontStep) * editorFontStep", write_session)
        self.assertIn("field.dispatchEvent(new Event('journl:editor-metrics-change'))", write_session)
        self.assertIn(
            "journl:editor-metrics-change",
            (ROOT_DIR / "app" / "static" / "write-hashtags.js").read_text(encoding="utf-8"),
        )
        self.assertIn("await window.handleSubmit({ backgroundAgent: true })", write_session)
        self.assertIn("KI-Auftrag wird vorbereitet", write_session)
        self.assertIn("KI-Funktion ist noch nicht bereit", write_session)
        self.assertNotIn("journl.write.sessions", write_session)
        self.assertNotIn("alert(", write_session)
        self.assertIn("confirm(", write_session)
        write_ai = (ROOT_DIR / "app" / "static" / "write-ai.js").read_text(encoding="utf-8")
        self.assertIn("restoreCurrentSession ? (data.content || '') : ''", write_ai)
        self.assertIn("window.resumeWriteAiSession = function", write_ai)
        journal_highlighting = (ROOT_DIR / "app" / "static" / "journal-highlighting.js").read_text(encoding="utf-8")
        self.assertIn("jt:(?:media|transcript)|journl-share-job", journal_highlighting)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            behavior = _template_behavior_source(template_name)
            self.assertIn("payload.write_session_id = writeSessionId", behavior)
            self.assertIn("window.markWriteSessionSubmitted(responseData)", behavior)
            self.assertIn("filename='write-history.js', v='20260822-9'", template)
            self.assertIn("filename='write-template-hashtags.js', v='20260814-1'", template)
            self.assertIn("filename='write-ai.js', v='20260829-2'", template)
            self.assertIn("filename='write-session.js', v='20261028-1'", template)
            self.assertIn("filename='write-voice.js', v='20261004-1'", template)
            self.assertIn("filename='whisper-model.js', v='20260824-1'", template)
            self.assertIn('id="settings-transcribe-model"', template)
            self.assertIn("filename='media-viewer.js', v='20260827-1'", template)
            self.assertIn("filename='screenshot-inbox.js', v='20260916-2'", template)
            self.assertIn("filename='share-api.js', v='20260824-1'", template)
            self.assertIn('id="settings-screenshot-token-create"', template)
            self.assertIn('id="settings-share-token-create"', template)
            self.assertIn('id="settings-ai-workflows"', template)
            self.assertIn("timeoutMs: 30000", behavior)
            self.assertIn("Senden hat zu lange gedauert", behavior)
            self.assertIn("filename='journal-highlighting.js', v='20260911-1'", template)
            self.assertIn("filename='write-read-mode.js', v='20260917-1'", template)
            self.assertIn("filename='brain.js', v='20261031-1'", template)
            self.assertIn('id="settings-transcribe-now"', template)
            self.assertIn('id="settings-transcribe-status"', template)
        desktop =(ROOT_DIR / "app" / "templates" / "desktop.html").read_text(encoding="utf-8")
        reveal = (ROOT_DIR / "app" / "static" / "write-controls-reveal.js").read_text(encoding="utf-8")
        self.assertIn("filename='write-controls-reveal.js'", desktop)
        self.assertIn(".write-session-actions .write-session-action.is-recording", reveal)
        self.assertIn("pointer-events: auto", reveal)
        self.assertIn("window.journlWriteMediaStrip?.mount(document.getElementById('input-area'))", write_session)
        self.assertIn("anchor.insertAdjacentElement('afterend', shell)", media_strip)
        self.assertIn("Foto angehängt", media_strip)
        self.assertIn("Wird beim Senden übernommen", media_strip)
        self.assertIn("sessionButton.classList.remove('text-green-400')", write_session)
        self.assertIn("const preview = item.preview", write_session)
        self.assertIn("max-h-8 overflow-hidden text-xs leading-4", write_session)
        self.assertIn("Im Hintergrund an KI senden", write_media_menu)
        self.assertNotIn("Im Hintergrund an Pi senden", write_media_menu)
        self.assertIn('data-refresh', write_session)
        screenshot_inbox = (ROOT_DIR / "app" / "static" / "screenshot-inbox.js").read_text(encoding="utf-8")
        self.assertIn("Hochgeladene Screenshots", screenshot_inbox)
        self.assertIn("writeSession.attachInboxItem", screenshot_inbox)
        self.assertIn("function localToday()", screenshot_inbox)
        self.assertIn("refreshSessions({ reconcileActive: true })", write_session)
        self.assertIn("/api/write-sessions/transcriptions/status", write_session)
        self.assertIn("Dokument anhängen", write_media_menu)
        self.assertIn("data-remove-media", media_strip)
        self.assertIn("chunk_count", write_session)
        self.assertIn("window.renderJournalMedia(item.media)", brain_client)
        self.assertIn("data-journal-image-viewer", media_strip)
        # Persisted journal media moved out of the session module into its own
        # file, which also owns the re-transcription control.
        journal_media = (ROOT_DIR / "app" / "static" / "journal-media.js").read_text(encoding="utf-8")
        self.assertIn("window.renderJournalMedia = function", journal_media)
        self.assertIn("item.media_url", journal_media)
        self.assertIn("data-journal-image-viewer", journal_media)
        self.assertIn("Medienanhang ist nicht verifizierbar", journal_media)
        self.assertNotIn("window.renderJournalMedia =", write_session)
        self.assertIn("DB-Hintergrundsession", brain_client)
        self.assertIn('class="text-gray-500"', journal_highlighting)
        mobile_behavior = _template_behavior_source("index.html")
        desktop_behavior = _template_behavior_source("desktop.html")
        for behavior in (mobile_behavior, desktop_behavior):
            self.assertIn('data-journal-calendar="dashboard"', behavior)
            self.assertNotIn("Journaldatum (JJJJ-MM-TT), leer für heute", behavior)
            self.assertIn("const date = dashboardJournalDate || new Date().toLocaleDateString('sv-SE');", behavior)
            self.assertNotIn("window.prompt('Journaldatum (JJJJ-MM-TT)'", behavior)
            self.assertIn('aria-label="Versionen dieses Tages öffnen"', behavior)
        journal_calendar = (ROOT_DIR / "app" / "static" / "journal-calendar.js").read_text(encoding="utf-8")
        self.assertIn("/api/journals/dates?month=", journal_calendar)
        self.assertIn("Journal vorhanden", journal_calendar)
        self.assertIn("input.dispatchEvent(new Event('change', { bubbles: true }))", journal_calendar)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('data-journal-calendar="brain-start"', template)
            self.assertIn('data-journal-calendar="brain-end"', template)
            self.assertIn("filename='journal-calendar.js', v='20260815-1'", template)
            core_asset = "desktop-app-core.js" if template_name == "desktop.html" else "mobile-app-core.js"
            core_version = "20261004-1" if template_name == "desktop.html" else "20261007-1"
            self.assertIn(f"filename='{core_asset}', v='{core_version}'", template)
        mobile = _shell_style_source("index.html")
        self.assertIn("#mobile-controls-scroll {\n  flex: 0 1 auto;\n  min-height: 0;\n  max-height: 55%;\n  padding-bottom: 0.5rem;", mobile)
        self.assertIn("#write-media-shell:not(.hidden) ~ #mobile-controls-scroll", mobile)
        self.assertIn("#write-screenshot-inbox-suggestion:not(.hidden) ~ #mobile-controls-scroll", mobile)
        self.assertIn('id="write-hashtag-quick-button"', mobile)
        self.assertIn('class="px-4 pt-3 pb-1"', mobile)
        self.assertIn("applyHashtagQuickButton(data.show_hashtag_quick_button !== false)", mobile_behavior)
        self.assertIn("window.writeEditorReplace(nextValue, { start: nextCursor, end: nextCursor })", mobile_behavior)
        self.assertNotIn('id="write-hashtag-quick-button"', desktop)
        self.assertNotIn("#editor-actions", mobile)

    def test_card_actions_are_bundled_in_the_shared_action_menu(self):
        menu = (ROOT_DIR / "app" / "static" / "action-menu.js").read_text(encoding="utf-8")
        family_board = (ROOT_DIR / "app" / "static" / "family-board.js").read_text(encoding="utf-8")
        brain_client = _brain_behavior_source()
        self.assertIn("window.actionMenu = function actionMenu", menu)
        self.assertIn("data-action-menu-trigger", menu)
        self.assertIn("data-action-menu-items", menu)
        # Eine einzelne Aktion bleibt ein Icon, sonst wäre das Menü ein Extraklick.
        self.assertIn("if (items.length <= 1) return items[0] || '';", menu)
        # Der Originalbutton wird geklickt, damit onclick und closest() greifen.
        self.assertIn("source.click();", menu)
        self.assertNotIn("source.cloneNode", menu)
        # Hovern öffnet das Menü, aber nur auf Zeigergeräten; die Hoverzone ist
        # größer als das Menü, damit es beim Zielen nicht wegklappt.
        self.assertIn("'(hover: hover) and (pointer: fine)'", menu)
        self.assertIn("HOVER_PADDING", menu)
        self.assertIn("function pointerInHoverZone", menu)
        # Beim Hovern darf kein Fokus wandern, sonst springt die Seite.
        self.assertIn("options.focus !== false", menu)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            behavior = _template_behavior_source(template_name)
            core_asset = "desktop-app-core.js" if template_name == "desktop.html" else "mobile-app-core.js"
            self.assertIn("filename='action-menu.js', v='20260914-1'", template)
            self.assertLess(
                template.index("filename='action-menu.js'"),
                template.index(f"filename='{core_asset}'"),
            )
            self.assertIn("(window.actionMenu || ((parts) => parts.join('')))([", behavior)
            self.assertIn('data-menu-variant="danger"', behavior)
        self.assertIn("(window.actionMenu || ((parts) => parts.join('')))(actions, { label: 'Aktionen' })", family_board)
        self.assertIn('data-menu-label="Einstellungen"', family_board)
        self.assertIn("function brainActionMenu(parts", brain_client)
        self.assertIn("brainActionMenu([", brain_client)

    def test_tailwind_ships_as_a_prebuilt_stylesheet_instead_of_the_runtime_cdn(self):
        """Der Kaltstart darf keine 407 kB Tailwind-Laufzeit mehr laden.

        Das fertige CSS ist ein Build-Artefakt: aendert sich eine Klasse in
        Templates oder Frontend-Skripten, ohne dass scripts/build-css.sh lief,
        weicht der Fingerprint ab und dieser Test wird rot - sonst fehlte im
        Browser still ein Style.
        """
        sys.path.insert(0, str(ROOT_DIR / "scripts" / "css"))
        import sources_hash

        css = ROOT_DIR / "app" / "static" / "tailwind.css"
        self.assertTrue(css.exists(), "app/static/tailwind.css fehlt - scripts/build-css.sh laufen lassen")
        recorded = (ROOT_DIR / "app" / "static" / "tailwind.css.sources").read_text(encoding="utf-8").strip()
        self.assertEqual(
            recorded,
            sources_hash.sources_hash(),
            "Klassen haben sich geaendert: scripts/build-css.sh laufen lassen und beide Dateien mitcommitten",
        )
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn("filename='tailwind.css', v='20261029-2'", template)
            self.assertNotIn("cdn.tailwindcss.com", template)
        main_source = (ROOT_DIR / "app" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("cdn.tailwindcss.com", main_source)

    def test_image_viewer_brings_its_own_zoom_because_the_shell_pins_the_viewport(self):
        viewer = (ROOT_DIR / "app" / "static" / "media-viewer.js").read_text(encoding="utf-8")
        mobile = (ROOT_DIR / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        # The mobile shell disables browser zoom on purpose, so the viewer has to
        # implement pinch, drag and double-tap itself.
        self.assertIn("user-scalable=no", mobile)
        self.assertIn("touch-action:none", viewer)
        self.assertIn("pointerdown", viewer)
        self.assertIn("kind: 'pinch'", viewer)
        self.assertIn("DOUBLE_TAP_SCALE", viewer)
        self.assertIn("function clampTranslation", viewer)
        # A zoomed photo must not close when the drag ends over the backdrop.
        self.assertIn("if (event.target === viewer && scale === 1) closeImageViewer();", viewer)
        # Fullscreen-App: der Rahmen laesst oben/unten die Safe Area frei, sonst
        # liegt der Schliessen-Button unter Notch bzw. Statusleiste.
        self.assertIn(
            "max-height:calc(100dvh - 2 * env(safe-area-inset-top, 0px) - 4.5rem)",
            viewer,
        )

    def test_persisted_voice_messages_render_as_strip_chips_and_can_be_redone(self):
        """Editing a Today entry shows media as the same compact chips as writing it."""
        journal_media = (ROOT_DIR / "app" / "static" / "journal-media.js").read_text(encoding="utf-8")
        source_edit = (ROOT_DIR / "app" / "static" / "write-source-edit.js").read_text(encoding="utf-8")
        write_session = (ROOT_DIR / "app" / "static" / "write-session.js").read_text(encoding="utf-8")
        media_strip = (ROOT_DIR / "app" / "static" / "write-media-strip.js").read_text(encoding="utf-8")

        # The editable text stops at the first media marker; the tail is carried
        # through verbatim so no marker, hash or transcript can be typed over.
        self.assertIn("splitMediaTail", journal_media)
        self.assertIn("window.journlJournalMedia?.splitMediaTail(framed.body)", source_edit)
        self.assertIn("mediaTail: split.tail", source_edit)
        self.assertIn("const tail = edit.block.mediaTail ? '\\n\\n' + edit.block.mediaTail : '';", source_edit)
        self.assertIn("renderSourceMedia(block.mediaItems)", source_edit)
        self.assertIn("window.setWriteSourceMedia?.(items || [])", source_edit)
        self.assertIn("window.journlJournalMedia?.chip(item, { openTranscript: state.openTranscript })", media_strip)
        self.assertIn("data-insert-transcript", journal_media)
        # Der abgeschnittene Chip-Vorschautext fuehrt zur vollen Lesefassung.
        self.assertIn("data-transcript-view", journal_media)

        # The re-transcription control and its endpoint.
        # A Today card keeps its player small and photos as thumbnails; the
        # entry text already shows the transcript, so only the editor chip
        # previews it and offers to insert it.
        self.assertIn("h-28 w-28 rounded-lg border border-gray-800 object-cover", journal_media)
        self.assertIn('class="w-full max-w-sm"', journal_media)
        self.assertIn("max-w-48 truncate text-gray-400", journal_media)
        self.assertIn("Text einfügen", journal_media)
        self.assertIn("data-retranscribe-media", journal_media)
        self.assertIn("/retranscribe", journal_media)
        self.assertIn("model: window.journlWhisperModel?.()", journal_media)
        # The edit stays open: only the media region changed, so it is rebased
        # in place instead of being closed and the typed text thrown away.
        self.assertIn("await window.rebaseWriteSourceEdit?.(mediaId)", journal_media)
        self.assertIn("window.rebaseWriteSourceEdit = rebaseDashboardEdit", source_edit)
        self.assertIn("edit.oldContent = fresh.content", source_edit)
        self.assertNotIn("!await window.saveWriteSourceEdit?.()", journal_media)

        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            behavior = _template_behavior_source(template_name)
            self.assertIn("filename='journal-media.js', v='20260915-1'", template)
            self.assertIn("window.renderJournalMedia(entry.media, { retranscribe: true })", behavior)

    def test_today_and_brain_edit_in_the_shared_write_context(self):
        source_edit = (ROOT_DIR / "app" / "static" / "write-source-edit.js").read_text(encoding="utf-8")
        write_session = (ROOT_DIR / "app" / "static" / "write-session.js").read_text(encoding="utf-8")
        brain_client = _brain_behavior_source()
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            behavior = _template_behavior_source(template_name)
            self.assertIn('id="write-edit-cancel"', template)
            self.assertIn("filename='write-source-edit.js', v='20261007-1'", template)
            self.assertIn("openDashboardWriteEditor('${editId}')", behavior)
            self.assertIn('class="flex items-center gap-3', behavior)
            self.assertNotIn("function toggleEdit(editId)", behavior)
        self.assertIn("window.openBrainWriteEditor || window.openBrainDocument", brain_client)
        self.assertIn("window.beginWriteSourceEdit", write_session)
        self.assertIn("sessionButton.textContent = '⋯'", write_session)
        # Attachments are collected in a carrier session and handed to the save.
        self.assertIn("window.writeSourceEditMedia", write_session)
        self.assertIn("markWriteSourceEditMediaSubmitted", write_session)
        self.assertIn("write_session_id: mediaSessionId", source_edit)
        self.assertIn("jt:media-placeholder", source_edit)
        self.assertIn("submitButton.textContent = active ? 'Speichern' : 'Senden'", source_edit)
        self.assertIn("window.openDashboardWriteEditor", source_edit)
        self.assertIn("window.openBrainWriteEditor", source_edit)
        self.assertIn("/api/write/template-preview", source_edit)
        self.assertIn("content_hash: brain.content_hash", source_edit)
        self.assertIn("window.endWriteSourceEdit", source_edit)

    # Familie-Notizen/-Projekte im Schreiben-Tab (write-source-edit.js,
    # write-snippets.js, family-search-Reihenfolge): siehe
    # tests/test_family_write_editor.py.

    def test_mobile_toast_stays_in_the_header_area(self):
        mobile = _shell_style_source("index.html")
        mobile_behavior = _template_behavior_source("index.html")
        self.assertIn("top: calc(var(--safe-top) + .75rem);", mobile)
        self.assertIn("bottom: auto;", mobile)
        self.assertIn("max-height: 4.75rem;", mobile)
        self.assertIn('id="toast" class="fixed top-3', mobile)
        self.assertIn("toast.className = `fixed top-3", mobile_behavior)
        self.assertNotIn("toast.className = `fixed bottom-6", mobile_behavior)

    def test_managed_history_preview_uses_explicit_buttons_and_stays_visible(self):
        for template_name in ("desktop.html", "index.html"):
            behavior = _template_behavior_source(template_name)
            self.assertIn("view.type = 'button'", behavior)
            self.assertIn("card.insertBefore(preview, list)", behavior)
            self.assertIn("preview.scrollIntoView({block:'nearest'})", behavior)

    def test_managed_history_shows_the_confirmed_media_count(self):
        for template_name in ("desktop.html", "index.html"):
            behavior = _template_behavior_source(template_name)
            self.assertIn("revision.media_count", behavior)
            self.assertIn("revision.media_integrity === 'verified'", behavior)
            self.assertIn("Medium' : ' Medien", behavior)

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

    def test_service_worker_is_served_from_the_root_scope(self):
        """A worker under /static/ could only control /static/, not the app shell."""
        with self.client.get("/sw.js") as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/javascript")
            self.assertEqual(response.headers["Cache-Control"], "no-cache")

    def test_the_service_worker_caches_static_assets_only(self):
        worker = (ROOT_DIR / "app" / "static" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("url.pathname.startsWith('/static/')", worker)
        self.assertIn("event.request.method !== 'GET'", worker)
        self.assertIn("url.origin !== self.location.origin", worker)
        # Caching an API response would let the worker serve another session's data.
        self.assertNotIn("/api/", worker)
        # Nur ?v=-URLs sind unveraenderlich und duerfen cache-first kommen; ein
        # Asset ohne Cache-Buster wuerde sonst fuer immer einfrieren.
        self.assertIn("const immutable = Boolean(url.searchParams.get('v'));", worker)
        self.assertIn("if (cached && immutable) return cached;", worker)
        self.assertIn("if (cached) return cached;", worker)
        # Der Cache-Name traegt die App-Version, damit activate() alte Staende
        # wegwerfen kann statt sie unbegrenzt anzusammeln.
        self.assertIn("new URL(self.location).searchParams.get('v')", worker)
        self.assertIn("key.startsWith('journl-static-')", worker)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            behavior = _template_behavior_source(template_name)
            self.assertIn("data-app-version=\"{{ APP_VERSION }}\"", template)
            self.assertIn("navigator.serviceWorker.register(path)", behavior)
            self.assertIn("document.documentElement.dataset.appVersion", behavior)
            self.assertIn("/sw.js?v=", behavior)

    def test_text_assets_are_gzipped_and_versioned_assets_are_cached_hard(self):
        """Kaltstart-Budget: das Frontend sind ~800 kB Text.

        Ungepackt und mit Revalidierung bei jedem Start war das der teuerste
        Teil des App-Starts im Mobilfunknetz.
        """
        with self.client.get(
            "/static/tailwind.css?v=20260829-3",
            headers={"Accept-Encoding": "gzip"},
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Content-Encoding"], "gzip")
            self.assertIn("Accept-Encoding", response.headers["Vary"])
            self.assertIn("immutable", response.headers["Cache-Control"])
            unpacked = gzip.decompress(response.get_data()).decode("utf-8")
        self.assertEqual(
            unpacked,
            (ROOT_DIR / "app" / "static" / "tailwind.css").read_text(encoding="utf-8"),
        )

        # Ohne ?v= fehlt der Cache-Buster, also darf auch nicht dauerhaft
        # gecacht werden.
        with self.client.get("/static/tailwind.css", headers={"Accept-Encoding": "gzip"}) as response:
            self.assertNotIn("immutable", response.headers.get("Cache-Control", ""))

        # Ohne Accept-Encoding bleibt die Antwort unveraendert lesbar.
        with self.client.get("/static/tailwind.css", headers={"Accept-Encoding": ""}) as response:
            self.assertNotIn("Content-Encoding", response.headers)
            self.assertTrue(response.get_data().startswith(b"*,:after"))

        # Bilder und Sprachnachrichten sind bereits komprimiert; sie erneut zu
        # packen kostet nur CPU.
        with self.client.get("/static/icon-192.png", headers={"Accept-Encoding": "gzip"}) as response:
            self.assertNotIn("Content-Encoding", response.headers)

    def test_returning_users_see_the_app_shell_without_waiting_for_the_auth_check(self):
        """Die Sitzung haelt vier Wochen - solange auf Login zu starren ist verlorene Zeit."""
        core = _template_behavior_source("index.html")
        self.assertIn("journl.was-authenticated", core)
        self.assertIn("if (wasAuthenticated) {", core)
        # Der Merker steuert nur die Anzeige; Zugriff gibt allein die Session.
        self.assertIn("rememberAuthentication(false);", core)
        # Config faehrt mit Health/CSRF zusammen los statt danach.
        self.assertIn("configPrefetch = wasAuthenticated", core)
        self.assertIn("if (!res || !res.ok) res = await apiFetch('/api/config');", core)

    def test_startup_requests_health_and_csrf_in_parallel(self):
        for template_name in ("desktop.html", "index.html"):
            behavior = _template_behavior_source(template_name)
            self.assertIn("const [res, csrfRes] = await Promise.all([", behavior)
            self.assertNotIn("const res = await fetch('/api/health'", behavior)

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
