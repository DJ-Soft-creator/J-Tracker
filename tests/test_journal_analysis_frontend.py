"""Verdrahtung der Journal-Analyse-Verwaltung in beiden Templates.

Desktop und Mobile werden bewusst getrennt gepflegt; dieser Test stellt sicher,
dass der eigenständige Burger-Eintrag nicht in einer der beiden Ansichten fehlt.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class JournalAnalysisFrontendTests(unittest.TestCase):
    def test_both_shells_carry_entry_section_and_script(self):
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('data-settings-open="analysis"', template, template_name)
            self.assertIn('data-settings-group="analysis"', template, template_name)
            self.assertIn("data-analysis-shell", template, template_name)
            self.assertIn("filename='journal-analysis.js'", template, template_name)

    def test_module_builds_its_panels_from_one_constant(self):
        source = (ROOT / "app" / "static" / "journal-analysis.js").read_text(encoding="utf-8")
        self.assertIn("const PANELS =", source)
        self.assertIn("data-analysis-panel", source)

    def test_both_shells_carry_the_notification_line(self):
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertIn('id="analysis-notify"', template, template_name)

    def test_job_list_offers_a_manual_run(self):
        source = (ROOT / "app" / "static" / "journal-analysis.js").read_text(encoding="utf-8")
        self.assertIn("data-analysis-run", source)
        self.assertIn("Ausführen", source)
        self.assertIn("/run", source)
        self.assertIn("/api/runs/", source)
        self.assertIn("data-analysis-run-cancel", source)
        self.assertIn("/cancel", source)
        self.assertIn("data-analysis-rerun", source)
        self.assertIn("Neu analysieren", source)

    def test_job_form_reads_models_explicitly(self):
        source = (ROOT / "app" / "static" / "journal-analysis.js").read_text(encoding="utf-8")
        self.assertIn("data-analysis-model-refresh", source)
        self.assertIn("Modelle auslesen", source)
        self.assertIn("/providers/", source)

    def test_module_reads_active_notifications_and_appends_to_the_write_box(self):
        source = (ROOT / "app" / "static" / "journal-analysis-notify.js").read_text(encoding="utf-8")
        self.assertIn("status=active", source)
        self.assertIn("appendToWrite", source)
        self.assertIn("simple-input", source)

    def test_sidecar_uses_the_analysis_provider_gate(self):
        """Lokal oder in der Anbieterverwaltung ausdruecklich freigegeben - sonst nichts."""
        source = (ROOT / "scripts" / "journal-analysis-scheduler.py").read_text(encoding="utf-8")
        self.assertIn("resolve_analysis_provider", source)
        self.assertNotIn("from historical_tagging import resolve_provider", source)
        manual = (ROOT / "app" / "journal_analysis_manual.py").read_text(encoding="utf-8")
        self.assertIn("resolve_analysis_provider", manual)

    def test_a_cloud_provider_in_a_job_shows_a_warning(self):
        source = (ROOT / "app" / "static" / "journal-analysis.js").read_text(encoding="utf-8")
        self.assertIn("provider.journal_analysis === true", source)
        self.assertIn("data-cloud-warning", source)
        self.assertIn("function syncCloudWarning(form)", source)

    def test_the_log_keeps_results_and_every_notification_of_a_run(self):
        """Nachhalten auch nach versehentlichem Abhaken: alle Hinweis-Status im Protokoll."""
        source = (ROOT / "app" / "static" / "journal-analysis.js").read_text(encoding="utf-8")
        self.assertIn("request(`${ENDPOINT}/notifications`)", source)  # ohne Status-Filter
        self.assertIn("done: 'abgehakt'", source)
        self.assertIn("textBlock('Ergebnis', run.result)", source)
        self.assertIn("function duration(run)", source)
        self.assertIn("data-log-filter", source)

    def test_write_tab_view_can_close_all_and_admin_list_keeps_recent(self):
        """Wegdrücken im Schreiben-Tab, Nachschauen 30 Tage in der Verwaltung."""
        source = (ROOT / "app" / "static" / "journal-analysis-notify.js").read_text(encoding="utf-8")
        self.assertIn("data-analysis-notify-close-all", source)
        self.assertIn("/notifications/close-all", source)
        self.assertIn("notifications?status=recent", source)
        self.assertIn("const RETENTION_DAYS = 30;", source)
        self.assertIn("button('open', 'Wieder öffnen')", source)
        backend = (ROOT / "app" / "journal_analysis.py").read_text(encoding="utf-8")
        self.assertIn("NOTIFICATION_RETENTION_DAYS = 30", backend)
        for template_name in ("desktop.html", "index.html"):
            template = (ROOT / "app" / "templates" / template_name).read_text(encoding="utf-8")
            self.assertLess(template.index("filename='journal-analysis-notify.js'"),
                            template.index("filename='journal-analysis.js'"), template_name)

    def test_module_is_not_admin_gated(self):
        source = (ROOT / "app" / "static" / "journal-analysis.js").read_text(encoding="utf-8")
        self.assertNotIn("current_user?.admin", source)
        self.assertNotIn("current_user.admin", source)


if __name__ == "__main__":
    unittest.main()