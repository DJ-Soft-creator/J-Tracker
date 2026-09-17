"""Trefferflaechen der mobilen Bedienelemente.

Auf dem Telefon wurden Ziele im Familien-Tab regelmaessig verfehlt und Aufgaben
beim Scrollen versehentlich abgehakt. Die Knoepfe sind deshalb groesser, duerfen
ihre Zeile aber nicht umbrechen lassen.
"""

import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class MobileControlTests(unittest.TestCase):
    def test_a_family_task_is_ticked_off_only_by_its_checkbox(self):
        """Beim Scrollen durch eine lange Liste traf ein Tipp sonst die Zeile.

        Die Aufgabenzeile ist deshalb kein Label mehr; nur das Kaestchen selbst
        schaltet, und es ist gross genug, um es mit dem Daumen zu treffen.
        """
        family_board = (ROOT_DIR / "app" / "static" / "family-board.js").read_text(encoding="utf-8")
        row = family_board[family_board.index("function familyBoardTaskRow"):]
        row = row[:row.index("\n    function ")]
        self.assertNotIn('<label class="flex items-start gap-2', row)
        self.assertIn('aria-label="Erledigt"', row)
        self.assertIn('class="h-5 w-5 accent-green-500', row)

    def test_the_family_tab_keeps_its_controls_on_one_line(self):
        """Groessere Knoepfe duerfen die Leiste nicht umbrechen lassen."""
        mobile = (ROOT_DIR / "app" / "templates" / "index.html").read_text(encoding="utf-8")
        toolbar = mobile[mobile.index('id="family-toolbar"'):]
        toolbar = toolbar[:toolbar.index("</div>")]
        self.assertIn("flex-nowrap", toolbar)
        self.assertNotIn("flex-wrap", toolbar)
        # Der Abbruch im Bearbeiten-Modus teilt sich die Zeile mit Senden und
        # Template-Wahl und bleibt deshalb bewusst schmal.
        cancel = mobile[mobile.index('id="write-edit-cancel"'):]
        self.assertIn("Ab..", cancel[:cancel.index("</button>")])
        self.assertIn('aria-label="Bearbeiten abbrechen"', cancel[:cancel.index(">")])


class DesktopSplitViewTests(unittest.TestCase):
    """Nebeneinander ist nur nuetzlich, wenn beide Spalten dieselbe Stelle zeigen."""

    def setUp(self):
        self.source = (ROOT_DIR / "app" / "static" / "write-read-mode.js").read_text(encoding="utf-8")

    def test_both_columns_scroll_together(self):
        self.assertIn("field.addEventListener('scroll', () => follow(field, pane));", self.source)
        self.assertIn("pane.addEventListener('scroll', () => follow(pane, field));", self.source)

    def test_the_sync_compares_proportionally_not_in_pixels(self):
        # Gerendertes Markdown ist nie genauso hoch wie sein Quelltext; ein
        # pixelgenauer Abgleich liefe deshalb schon nach wenigen Zeilen weg.
        self.assertIn("element.scrollHeight - element.clientHeight", self.source)
        self.assertIn("scrollRatio(source) * range", self.source)

    def test_a_programmatic_scroll_cannot_bounce_back(self):
        self.assertIn("followLock", self.source)
        self.assertIn("window.requestAnimationFrame(() => { followLock = false; });", self.source)

    def test_rerendering_keeps_the_reading_column_at_the_written_place(self):
        render = self.source[self.source.index("function render()"):]
        self.assertIn("if (isSplit()) follow(field, pane);", render[:render.index("\n  // ")])


class WriteReadModeTests(unittest.TestCase):
    """Ein Tab, in dem man nicht schreiben kann, darf nur auf Ansage wiederkommen."""

    def setUp(self):
        self.source = (ROOT_DIR / "app" / "static" / "write-read-mode.js").read_text(encoding="utf-8")

    def test_the_tab_starts_in_the_view_the_setting_names(self):
        self.assertIn("const START_KEY = 'journl.write.start-view';", self.source)
        self.assertIn("let mode = startMode();", self.source)

    def test_an_unknown_or_missing_setting_starts_in_the_writing_mode(self):
        stored = self.source[self.source.index("function startMode()"):]
        self.assertIn("MODES.includes(value) ? value : 'edit'", stored[:stored.index("\n  }")])

    def test_switching_in_the_dots_menu_is_not_remembered(self):
        # Die Lesefassung sieht aus wie das Schreibfeld, nimmt aber keine
        # Eingabe an. Als der zuletzt benutzte Modus noch von selbst zurueckkam,
        # half auf dem Handy auch das Abschiessen der PWA nicht mehr heraus.
        switch = self.source[self.source.index("function setMode(next, options)"):]
        self.assertNotIn("localStorage", switch[:switch.index("\n  }")])

    def test_the_start_view_is_settable_per_device_in_both_shells(self):
        # Nur diese Datei weiss, welche Ansichten eine Shell kann - auf dem
        # Handy zwei, auf dem Desktop drei -, also fuellt sie die Auswahl selbst.
        self.assertIn("document.getElementById('settings-start-view')", self.source)
        self.assertIn("startSelect.innerHTML = MODES", self.source)
        for name in ("index.html", "desktop.html"):
            template = (ROOT_DIR / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertIn('id="settings-start-view"', template)
            self.assertIn("Ansicht beim Start", template)


class WriteDotsMenuTests(unittest.TestCase):
    """Das "..."-Menue des Schreiben-Tabs muss auf ein Telefon passen.

    Es sammelt Eintraege aus sechs Modulen und war dadurch hoeher als der
    Bildschirm: auf dem iPhone lag der oberste Eintrag hinter der Dynamic
    Island und war weder lesbar noch antippbar.
    """

    def setUp(self):
        self.source = (ROOT_DIR / "app" / "static" / "write-media-menu.js").read_text(encoding="utf-8")
        self.position = self.source[self.source.index("function positionMediaMenu()"):]
        self.position = self.position[:self.position.index("\n  }")]

    def test_the_menu_stays_between_header_and_tab_bar(self):
        # Beide Leisten liegen bereits innerhalb der Safe Area; an ihnen zu
        # messen ist verlaesslicher als an der reinen Fensterhoehe.
        self.assertIn("document.getElementById('app-header')", self.position)
        self.assertIn("document.querySelector('#app > nav')", self.position)
        self.assertIn("Math.max(topLimit, rect.top - margin - height)", self.position)

    def test_a_menu_taller_than_the_gap_scrolls_instead_of_being_cut_off(self):
        self.assertIn("mediaMenu.style.maxHeight = `${Math.round(height)}px`", self.position)
        self.assertIn("const height = Math.min(naturalHeight, available);", self.position)
        self.assertIn("overflow-y-auto", self.source)
        # Eine feste 80vh-Kappe half nicht: sie begann trotzdem oben am Rand.
        self.assertNotIn("max-h-[80vh] overflow-y-auto rounded-lg", self.source)

    def test_rarely_used_entries_are_folded_away_and_stay_where_they_were_left(self):
        self.assertIn("menuGroupMarkup('attach', 'Anhängen'", self.source)
        self.assertIn("menuGroupMarkup('view', 'Darstellung'", self.source)
        self.assertIn("data-menu-group-body=\"${name}\" class=\"hidden\"", self.source)
        self.assertIn("journl.write.menu-group.", self.source)
        # Aufklappen aendert die Hoehe - ohne neue Ausrichtung wandert das
        # Menue wieder unter die Kopfleiste.
        toggle = self.source[self.source.index("mediaMenu.querySelectorAll('[data-menu-group-toggle]')"):]
        self.assertIn("positionMediaMenu();", toggle[:toggle.index("\n  });")])

    def test_the_menu_shell_is_its_own_module_the_entries_only_fill(self):
        write_session = (ROOT_DIR / "app" / "static" / "write-session.js").read_text(encoding="utf-8")
        self.assertIn("window.journlWriteMediaMenu", self.source)
        self.assertIn("const menu = window.journlWriteMediaMenu;", write_session)
        self.assertIn("mediaButton.addEventListener('click', () => menu.toggle(mediaButton));", write_session)
        for name in ("index.html", "desktop.html"):
            template = (ROOT_DIR / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertLess(
                template.index("filename='write-media-menu.js'"),
                template.index("filename='write-session.js'"),
            )

    def test_the_other_modules_hang_their_entries_in_those_groups(self):
        screenshot_inbox = (ROOT_DIR / "app" / "static" / "screenshot-inbox.js").read_text(encoding="utf-8")
        read_mode = (ROOT_DIR / "app" / "static" / "write-read-mode.js").read_text(encoding="utf-8")
        self.assertIn("mediaMenu.querySelector('[data-menu-group-body=\"attach\"]')", screenshot_inbox)
        # Die Ansicht gehoert neben die Schriftgroesse in die Gruppe, nicht auf
        # die oberste Ebene - insertBefore am Menue wuerde dort ausserdem
        # scheitern, weil die Schrift-Sektion kein direktes Kind mehr ist.
        self.assertIn("fontSection.insertAdjacentElement('beforebegin', section)", read_mode)
        self.assertNotIn("menu.insertBefore(section, fontSection)", read_mode)


if __name__ == "__main__":
    unittest.main()
