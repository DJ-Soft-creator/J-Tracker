"""Familie-Notizen/-Projekte als Markdown im Schreiben-Tab: Frontend-Asserts.

Split aus test_pwa.py (Größenregel, tests/test_code_structure.py). Server-
seitige Rundläufe stehen in test_family_board_api.py; hier nur die
JS/Template-Assertions, die das Verhalten der Editor-Integration absichern.
"""

from pathlib import Path
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]


class FamilyWriteEditorAssetTests(unittest.TestCase):
    def test_family_markdown_editor_hides_task_and_comment_machine_fields(self):
        """The raw pipe format (id/user/target-date/...) rides along hidden;
        only a matched title gets it back on save (write-source-edit.js).
        See tests/test_family_board_api.py for the server-side round trip."""
        source_edit = (ROOT_DIR / "app" / "static" / "write-source-edit.js").read_text(encoding="utf-8")
        self.assertIn("function familySimplifyBody(body)", source_edit)
        self.assertIn("function familyReinflateBody(editedBody, originalTasks)", source_edit)
        self.assertIn("editableBlock(descriptor.editorContent, descriptor.source)", source_edit)
        self.assertIn("familyReinflateBody(field.value, edit.block.familyTasks)", source_edit)

    def test_the_machine_headings_stay_out_of_the_editor(self):
        """"## Aufgaben"/"## Kommentare" sind Ablagefaecher, keine Gliederung.

        Sichtbar sorgten sie dafuer, dass jede unten angehaengte Zeile als
        Kommentar gespeichert wurde - und Kommentare standen nie auf der Karte.
        Der Editor zeigt jetzt durchgehenden Text; eine Aufgabe ist eine Zeile
        mit Kaestchen, den Rahmen setzt erst das Speichern wieder ein.
        """
        source_edit = (ROOT_DIR / "app" / "static" / "write-source-edit.js").read_text(encoding="utf-8")
        simplify = source_edit[source_edit.index("function familySimplifyBody"):
                               source_edit.index("function familyReinflateBody")]
        self.assertNotIn("'## Aufgaben'", simplify)
        self.assertNotIn("'## Kommentare'", simplify)
        reinflate = source_edit[source_edit.index("function familyReinflateBody"):
                                source_edit.index("function editableBlock")]
        self.assertIn("'## Aufgaben'", reinflate)
        self.assertIn("'## Kommentare'", reinflate)

    def test_write_snippet_bar_is_reachable_on_both_shells(self):
        # Regression: on desktop #write-snippet-bar used to sit inside
        # #input-surface (overflow:hidden, #simple-input fills height:100%)
        # and was rendered but never visible; must be a sibling instead.
        source = (ROOT_DIR / "app" / "static" / "write-snippets.js").read_text(encoding="utf-8")
        self.assertIn("field.addEventListener('keydown'", source)
        self.assertIn("applySnippet(top)", source)
        desktop = (ROOT_DIR / "app" / "templates" / "desktop.html").read_text(encoding="utf-8")
        surface_end = desktop.index('id="input-surface"')
        surface_end = desktop.index("</div>", desktop.index('id="input-resize-controls"', surface_end))
        bar_pos = desktop.index('id="write-snippet-bar"')
        selector_pos = desktop.index('id="project-selector-container"')
        self.assertGreater(bar_pos, surface_end, "write-snippet-bar liegt noch innerhalb von #input-surface")
        self.assertLess(bar_pos, selector_pos)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT_DIR / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('id="write-snippet-bar"', template)
            self.assertIn("filename='write-snippets.js'", template)
        # Kachel zeigt Auslöser UND einzusetzenden Text, nicht nur Auslöser.
        self.assertIn("${escapeHtml(s.expansion)", source)

    def test_family_search_listener_does_not_reference_family_board_too_early(self):
        # Regression: renderFamily lebt in family-board.js, das nach den
        # *-app-features.js-Dateien laedt. Ein direkter Verweis beim
        # Registrieren warf beim Seitenaufbau "renderFamily is not defined"
        # und riss - auf Desktop als letzte Zeile der Datei - jede weitere
        # top-level-Initialisierung danach mit ab.
        for asset in ("desktop-app-features.js", "mobile-app-features.js"):
            source = (ROOT_DIR / "app" / "static" / asset).read_text(encoding="utf-8")
            self.assertNotIn("'input', renderFamily)", source)
            self.assertIn("'input', () => renderFamily())", source)


if __name__ == "__main__":
    unittest.main()
