"""Die faltbare Anhang-Leiste des Schreiben-Tabs (`write-media-strip.js`)."""

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _read(*parts):
    return (ROOT_DIR.joinpath(*parts)).read_text(encoding="utf-8")


class WriteMediaStripTests(unittest.TestCase):
    def test_attachments_fold_into_one_small_bar(self):
        """Alle Anhänge lassen sich gesammelt zu einer kleinen Pille einklappen."""
        media_strip = _read("app", "static", "write-media-strip.js")
        for needle in (
            "shell.id = 'write-media-shell'",
            "toggle.id = 'write-media-toggle'",
            "shell.append(toggle, strip, reader)",
            # Die Entscheidung überlebt Neuladen und Gerätewechsel im Browser.
            "journl.write.attachments-collapsed",
            "collapsed = !collapsed",
            "strip.classList.toggle('hidden', !visible || collapsed)",
            # Eingeklappt wird nichts gerendert: keine Vorschau, kein Audio.
            "strip.innerHTML = '';",
            # Auch eingeklappt bleibt sichtbar, was mitgeht und was hängt.
            "function summaryText(items, uploadingLabel)",
            "Sprachnachrichten",
            "fehlgeschlagen`",
            "aria-expanded",
        ):
            self.assertIn(needle, media_strip)

    def test_session_module_only_feeds_the_strip(self):
        write_session = _read("app", "static", "write-session.js")
        screenshot_inbox = _read("app", "static", "screenshot-inbox.js")
        self.assertIn("window.journlWriteMediaStrip?.mount(", write_session)
        self.assertIn("mediaStrip.render({", write_session)
        self.assertNotIn("strip.innerHTML =", write_session)
        # Der Screenshot-Vorschlag hängt unter der Hülle, nicht in ihr: sonst
        # verstecken eingeklappte Anhänge ihn mit.
        self.assertIn("mediaStrip.closest('#write-media-shell')", screenshot_inbox)

    def test_a_transcript_can_be_read_in_full_before_and_after_sending(self):
        """Das Transkript ist im Schreiben-Tab ganz lesbar, nicht nur abgeschnitten."""
        media_strip = _read("app", "static", "write-media-strip.js")
        journal_media = _read("app", "static", "journal-media.js")
        write_session = _read("app", "static", "write-session.js")
        for needle in (
            "reader.id = 'write-media-transcript'",
            "function renderReader(persisted, media)",
            # Ganzer Text: umgebrochen und bei Bedarf scrollbar, damit der
            # Editor darüber nicht zusammenschrumpft.
            "max-h-[30vh] overflow-y-auto whitespace-pre-wrap break-words",
            "data-transcript-view",
            "data-transcript-close",
            # Eingeklappte Anhänge zeigen auch keine Lesefassung.
            "reader.classList.toggle('hidden', !visible)",
            "function showTranscript(mediaId)",
        ):
            self.assertIn(needle, media_strip)
        # Beide Chip-Sorten führen zur selben Lesefassung: die neue Aufnahme
        # und die schon im Eintrag abgelegte.
        self.assertIn("data-transcript-view", journal_media)
        self.assertIn("chip(item, { openTranscript: state.openTranscript })", media_strip)
        # Nach dem Transkribieren öffnet sie sich von selbst; in den Editor
        # kopiert wird der Text weiterhin nicht.
        self.assertIn("mediaStrip.showTranscript?.(mediaId)", write_session)
        self.assertNotIn("appendTranscriptToEditor(updated", write_session)

    def test_shell_layout_rules_follow_the_wrapper(self):
        mobile = _read("app", "static", "mobile-shell.css")
        self.assertIn("#write-media-shell {", mobile)
        self.assertIn("#write-media-shell:not(.hidden) ~ #write-actions-row", mobile)
        self.assertIn("#write-media-shell:not(.hidden) ~ #mobile-controls-scroll", mobile)
        for template_name in ("desktop.html", "index.html"):
            template = _read("app", "templates", template_name)
            self.assertIn("filename='write-media-strip.js', v='20261003-1'", template)
            self.assertLess(
                template.index("filename='write-media-strip.js'"),
                template.index("filename='write-session.js'"),
            )


if __name__ == "__main__":
    unittest.main()
